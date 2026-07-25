"""确定性流水线引擎。

「事实层确定、语义层自主」：本文件是纯确定性状态机，LLM 只在语义节点被调用。
每个阶段 = 一个事务（落库即 checkpoint），任意时刻崩溃/中断后 resume 都从
最后完成的阶段继续，已产出内容绝不重算。

章节状态机：planned -> check(先经 draft) -> [revise -> check]* -> deslop -> extract -> summarize -> final
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import context as ctx
from . import state as st
from .config import Config, load_config
from .db import DB
from .llm import LLMClient
from .prompts import PromptLoader
from .util import extract_json, strip_fences


class EngineError(Exception):
    pass


class Engine:
    def __init__(self, project_dir: str | Path, llm: LLMClient | None = None,
                 on_event: Callable[[str], None] | None = None):
        self.dir = Path(project_dir)
        self.cfg: Config = load_config(self.dir)
        self.db = DB(self.dir / "book.db")
        self.llm = llm or LLMClient(self.cfg, self.db)
        self.prompts = PromptLoader(self.dir)
        self.on_event = on_event or (lambda msg: None)
        self.stop_event = threading.Event()
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger(f"gennovel.{self.dir.resolve()}")
        if not logger.handlers:
            logs = self.dir / "logs"
            logs.mkdir(exist_ok=True)
            h = RotatingFileHandler(logs / "engine.log", maxBytes=2_000_000,
                                    backupCount=3, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(h)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        return logger

    def close(self):
        self.db.close()

    def request_stop(self):
        """请求优雅停止：当前阶段完成落库后停在阶段边界，进度零丢失。"""
        self.stop_event.set()

    def _emit(self, msg: str):
        self.logger.info(msg)
        self.on_event(msg)

    def _chat_json(self, stage: str, system: str, user: str, chapter_idx=None,
                   retries: int = 1) -> dict:
        """JSON 阶段调用：解析失败自动重试一次（追加纠错指令）。"""
        out = self.llm.chat(stage, system, user, chapter_idx)
        data = extract_json(out)
        for _ in range(retries):
            if data is not None:
                break
            out = self.llm.chat(
                stage, system,
                user + "\n\n（你上一次的输出不是合法 JSON，请只输出一个合法 JSON 对象。）",
                chapter_idx,
            )
            data = extract_json(out)
        if data is None:
            raise EngineError(f"[{stage}] LLM 输出无法解析为 JSON")
        return data

    # ---------------- 书籍级阶段 ----------------

    def ensure_bible(self) -> dict:
        bible = self.db.get_meta("bible")
        if bible:
            return bible
        if not self.cfg.book.premise.strip() or self.cfg.book.premise.startswith("（必填）"):
            raise EngineError("请先在 gennovel.yaml 的 book.premise 填写故事前提")
        self._emit("生成设定集（bible）……")
        system, user = self.prompts.render(
            "bible",
            premise=self.cfg.book.premise, genre=self.cfg.book.genre,
            style=self.cfg.book.style, target_chapters=self.cfg.book.target_chapters,
            words_per_chapter=self.cfg.book.words_per_chapter,
        )
        bible = self._chat_json("bible", system, user)
        for key in ("title", "characters", "locked_facts"):
            if key not in bible:
                raise EngineError(f"设定集缺少必要字段 {key}，请重试或调整 premise")
        with self.db.tx() as conn:
            self.db.set_meta("bible", bible, conn)
            self.db.set_meta("state", st.init_state_from_bible(bible), conn)
            self.db.set_meta("long_summary", "", conn)
            self.db.snapshot("bible", "init", bible, conn)
        self._emit(f"设定集完成：《{bible['title']}》，角色 {len(bible['characters'])} 个")
        return bible

    def ensure_arc(self):
        arcs = self.db.arcs()
        planned = self.db.count_chapters()
        target = self.cfg.book.target_chapters
        if planned >= target:
            return
        total_est = sum(a["chapters_estimate"] for a in arcs)
        if arcs and planned < total_est:
            return  # 当前弧还有未规划的章
        bible = self.db.get_meta("bible")
        state = self.db.get_meta("state")
        next_idx = len(arcs) + 1
        remaining = target - planned
        self._emit(f"滚动规划第 {next_idx} 弧（剩余 {remaining} 章）……")
        arcs_done = "\n".join(
            f"第{a['idx']}弧《{a['title']}》：{a['plan'].get('goal','')}（出口：{a['plan'].get('exit_state','')}）"
            for a in arcs
        ) or "（尚无，这是第一弧）"
        system, user = self.prompts.render(
            "arcs",
            bible_core=ctx.bible_core(bible, include_ending=True),
            state_brief=st.brief(state),
            long_summary=self.db.get_meta("long_summary", "") or "（无）",
            arcs_done=arcs_done,
            remaining_chapters=remaining,
            target_chapters=target,
            next_arc_idx=next_idx,
        )
        plan = self._chat_json("arcs", system, user)
        est = int(plan.get("chapters_estimate", 0) or 0)
        est = max(1, min(est, remaining))
        with self.db.tx() as conn:
            self.db.add_arc(next_idx, plan.get("title", f"第{next_idx}弧"), plan, est, conn)
        self._emit(f"第 {next_idx} 弧《{plan.get('title','')}》规划完成，预计 {est} 章")

    def ensure_beats(self):
        if self.db.next_unfinished_chapter():
            return
        planned = self.db.count_chapters()
        target = self.cfg.book.target_chapters
        if planned >= target:
            return
        arc = self.db.arcs()[-1]
        arc_written = self.db.count_chapters(arc_idx=arc["idx"])
        n = min(self.cfg.engine.batch_plan_chapters,
                arc["chapters_estimate"] - arc_written,
                target - planned)
        if n <= 0:
            return  # 本弧排满，交给下一轮 ensure_arc
        bible = self.db.get_meta("bible")
        state = self.db.get_meta("state")
        start_idx = planned + 1
        self._emit(f"规划第 {start_idx}~{start_idx + n - 1} 章节拍……")
        system, user = self.prompts.render(
            "beats",
            arc_plan=json.dumps(arc["plan"], ensure_ascii=False),
            bible_core=ctx.bible_core(bible),
            state_brief=st.brief(state),
            recent_summaries=ctx.recent_summaries(self.db, start_idx),
            arc_written=arc_written, arc_estimate=arc["chapters_estimate"],
            n_chapters=n, start_idx=start_idx,
        )
        data = self._chat_json("beats", system, user)
        chapters = data.get("chapters", [])
        if not chapters:
            raise EngineError("节拍规划返回空章节列表")
        with self.db.tx() as conn:
            for offset, chp in enumerate(chapters[:n]):
                idx = start_idx + offset  # 章号以引擎为准，不信任模型
                self.db.add_chapter(idx, arc["idx"], chp.get("title", f"第{idx}章"), chp, conn)
        self._emit(f"节拍规划完成 {min(len(chapters), n)} 章")

    # ---------------- 章节级阶段 ----------------

    def process_to_final(self, idx: int) -> bool:
        """推进单章到 final。收到停止请求时停在阶段边界，返回 False。"""
        while True:
            ch = self.db.chapter(idx)
            if ch is None:
                raise EngineError(f"第 {idx} 章不存在")
            if ch["status"] == "final":
                return True
            if self.stop_event.is_set():
                self._emit(f"已在第 {idx} 章的 {ch['status']} 阶段边界优雅停止")
                return False
            handler = {
                "planned": self._stage_draft,
                "check": self._stage_check,
                "revise": self._stage_revise,
                "deslop": self._stage_deslop,
                "extract": self._stage_extract,
                "summarize": self._stage_summarize,
            }.get(ch["status"])
            if handler is None:
                raise EngineError(f"未知章节状态: {ch['status']}")
            handler(ch)

    def _stage_draft(self, ch: dict):
        self._emit(f"第 {ch['idx']} 章《{ch['title']}》起草……")
        bible = self.db.get_meta("bible")
        state = self.db.get_meta("state")
        arc = next(a for a in self.db.arcs() if a["idx"] == ch["arc_idx"])
        variables = ctx.build_draft_vars(self.cfg, self.db, bible, state, ch, arc,
                                         self.prompts.prose_rules())
        system, user = self.prompts.render("draft", **variables)
        text = strip_fences(self.llm.chat("draft", system, user, ch["idx"]))
        if len(text) < 300:
            raise EngineError(f"第 {ch['idx']} 章草稿过短（{len(text)} 字），中止以待排查")
        with self.db.tx() as conn:
            self.db.update_chapter(ch["idx"], conn, draft=text, words=len(text), status="check")

    def _stage_check(self, ch: dict):
        self._emit(f"第 {ch['idx']} 章审校（第 {ch['revise_round'] + 1} 轮）……")
        bible = self.db.get_meta("bible")
        state = self.db.get_meta("state")
        system, user = self.prompts.render(
            "check",
            locked_facts=ctx.locked_facts(bible),
            state_brief=st.brief(state),
            open_foreshadows=st.open_foreshadows(state),
            idx=ch["idx"], title=ch["title"],
            beats=ctx.format_beats(ch["plan"]), hook=ch["plan"].get("hook", ""),
            draft=ch["draft"], min_avg_score=self.cfg.engine.min_avg_score,
        )
        warn = ch["warn"]
        try:
            report = self._chat_json("check", system, user, ch["idx"])
        except EngineError:
            report = {"issues": [], "scores": {}, "verdict": "pass"}
            warn = (warn + "; check 输出解析失败，本章未经审校").strip("; ")

        scores = [v for v in (report.get("scores") or {}).values() if isinstance(v, (int, float))]
        avg = sum(scores) / len(scores) if scores else 10.0
        has_sev3 = any(i.get("severity") == 3 for i in report.get("issues", []))
        needs_revise = report.get("verdict") == "revise" or has_sev3 or avg < self.cfg.engine.min_avg_score

        if needs_revise and ch["revise_round"] < self.cfg.engine.max_revise_rounds:
            next_status = "revise"
        else:
            if needs_revise:
                warn = (warn + f"; 达到最大修订轮数仍未通过（均分 {avg:.1f}）").strip("; ")
            next_status = "deslop"
        with self.db.tx() as conn:
            self.db.update_chapter(ch["idx"], conn, check=report, warn=warn, status=next_status)
        self._emit(f"第 {ch['idx']} 章审校完成：均分 {avg:.1f}，"
                   f"{'需修订' if next_status == 'revise' else '进入清洁'}")

    def _stage_revise(self, ch: dict):
        self._emit(f"第 {ch['idx']} 章修订……")
        bible = self.db.get_meta("bible")
        issues = "\n".join(
            f"- [{i.get('category','?')}|严重度{i.get('severity','?')}] {i.get('desc','')}"
            f"（证据：{i.get('evidence','')}；建议：{i.get('fix_hint','')}）"
            for i in ch["check"].get("issues", [])
        ) or "（无具体条目，请整体提升文笔与节奏）"
        system, user = self.prompts.render(
            "revise",
            locked_facts=ctx.locked_facts(bible),
            idx=ch["idx"], title=ch["title"],
            beats=ctx.format_beats(ch["plan"]), hook=ch["plan"].get("hook", ""),
            issues=issues, draft=ch["draft"],
            prose_rules=self.prompts.prose_rules(),
        )
        text = strip_fences(self.llm.chat("revise", system, user, ch["idx"]))
        if len(text) < len(ch["draft"]) * 0.5:
            # 修订稿异常缩水视为失败，保留原稿直接进入下一阶段并记警告
            with self.db.tx() as conn:
                self.db.update_chapter(ch["idx"], conn, status="deslop",
                                       warn=(ch["warn"] + "; 修订稿异常缩水，保留原稿").strip("; "))
            return
        with self.db.tx() as conn:
            self.db.snapshot("draft", f"ch{ch['idx']}_r{ch['revise_round']}", ch["draft"], conn)
            self.db.update_chapter(ch["idx"], conn, draft=text, words=len(text),
                                   revise_round=ch["revise_round"] + 1, status="check")

    def _stage_deslop(self, ch: dict):
        self._emit(f"第 {ch['idx']} 章去AI味清洁……")
        bible = self.db.get_meta("bible")
        system, user = self.prompts.render(
            "deslop",
            style_card=json.dumps(bible.get("style_card", {}), ensure_ascii=False),
            draft=ch["draft"],
        )
        text = strip_fences(self.llm.chat("deslop", system, user, ch["idx"]))
        warn = ch["warn"]
        ratio = len(text) / max(len(ch["draft"]), 1)
        if not (0.6 <= ratio <= 1.4):
            text = ch["draft"]  # 清洁稿长度异常 -> 不可信，保留原稿
            warn = (warn + f"; 清洁稿长度异常(x{ratio:.2f})，保留原稿").strip("; ")
        with self.db.tx() as conn:
            self.db.update_chapter(ch["idx"], conn, final=text, words=len(text),
                                   warn=warn, status="extract")

    def _stage_extract(self, ch: dict):
        self._emit(f"第 {ch['idx']} 章状态抽取……")
        state = self.db.get_meta("state")
        system, user = self.prompts.render(
            "extract",
            state_brief=st.brief(state),
            open_foreshadows=st.open_foreshadows(state),
            idx=ch["idx"], final_text=ch["final"],
        )
        warn = ch["warn"]
        try:
            ext = self._chat_json("extract", system, user, ch["idx"])
        except EngineError:
            ext = {}
            warn = (warn + "; extract 解析失败，本章未更新状态").strip("; ")
        new_state, apply_warns = st.apply_extract(state, ext, ch["idx"]) if ext else (state, [])
        if apply_warns:
            warn = (warn + "; " + "；".join(apply_warns)).strip("; ")
        with self.db.tx() as conn:
            self.db.snapshot("state", f"before_ch{ch['idx']}", state, conn)
            self.db.set_meta("state", new_state, conn)
            self.db.update_chapter(ch["idx"], conn, warn=warn, status="summarize")

    def _stage_summarize(self, ch: dict):
        self._emit(f"第 {ch['idx']} 章摘要归档……")
        system, user = self.prompts.render(
            "summarize", idx=ch["idx"], title=ch["title"], final_text=ch["final"],
        )
        try:
            data = self._chat_json("summarize", system, user, ch["idx"])
            summary = data.get("summary", "")
            key_events = data.get("key_events", [])
        except EngineError:
            summary, key_events = ch["final"][:200], []
        fts_text = summary + " " + " ".join(key_events)

        long_summary = self.db.get_meta("long_summary", "") or ""
        long_summary = (long_summary + f"\n第{ch['idx']}章：{summary}").strip()
        # 每 K 章压缩一次长摘要，防止无限膨胀
        if ch["idx"] % self.cfg.engine.long_summary_compress_every == 0:
            budget_chars = 3000
            sys2, user2 = self.prompts.render(
                "long_summary", long_summary=long_summary,
                new_summaries="（已并入上文）", budget_chars=budget_chars,
            )
            try:
                long_summary = strip_fences(self.llm.chat("summarize", sys2, user2, ch["idx"]))
            except Exception as e:  # 压缩失败不致命，下轮再压
                self._emit(f"长摘要压缩失败（忽略，下轮再压）: {e}")

        with self.db.tx() as conn:
            self.db.update_chapter(ch["idx"], conn, summary=summary, status="final")
            self.db.index_chapter(ch["idx"], ch["title"], fts_text, conn)
            self.db.set_meta("long_summary", long_summary, conn)
        self._emit(f"第 {ch['idx']} 章完成（{ch['words']} 字）")

    # ---------------- 顶层入口 ----------------

    def run(self, n_chapters: int = 1) -> int:
        """推进 n 章到 final。可重入：从任何断点继续。返回本次完成章数。"""
        self.ensure_bible()
        done = 0
        while done < n_chapters and not self.stop_event.is_set():
            if self.db.count_chapters(final_only=True) >= self.cfg.book.target_chapters:
                self._emit("已达到目标章数，全书完成")
                break
            self.ensure_arc()
            self.ensure_beats()
            ch = self.db.next_unfinished_chapter()
            if ch is None:
                if self.db.count_chapters() >= self.cfg.book.target_chapters:
                    break
                continue  # 刚补完规划，回到循环头
            if not self.process_to_final(ch["idx"]):
                break  # 优雅停止
            done += 1
        return done

    def redo(self, idx: int, from_stage: str = "planned"):
        """把某章重置回指定阶段重跑（final 章会连带提示状态可能已被其污染）。"""
        ch = self.db.chapter(idx)
        if ch is None:
            raise EngineError(f"第 {idx} 章不存在")
        allowed = {"planned", "check", "deslop", "extract", "summarize"}
        if from_stage not in allowed:
            raise EngineError(f"from_stage 须为 {allowed}")
        fields: dict = {"status": from_stage, "revise_round": 0, "warn": ""}
        if from_stage == "planned":
            fields.update(draft="", final="", summary="", check={})
        with self.db.tx() as conn:
            kept = {k: ch[k] for k in ("draft", "final", "summary")}
            self.db.snapshot("chapter", f"redo_ch{idx}", kept, conn)
            self.db.update_chapter(idx, conn, **fields)
        if ch["status"] == "final":
            self._emit(f"注意：第 {idx} 章此前已完成，其状态抽取结果仍在故事状态中；"
                       f"如需彻底回滚请用 snapshots 里的 before_ch{idx} 状态快照")

    def status(self) -> dict:
        chapters = self.db.chapters()
        finals = [c for c in chapters if c["status"] == "final"]
        bible = self.db.get_meta("bible") or {}
        prices = {p.name: (p.price_in, p.price_out) for p in self.cfg.providers}
        cost = sum(
            u["pt"] / 1e6 * prices.get(u["provider"], (0, 0))[0]
            + u["ct"] / 1e6 * prices.get(u["provider"], (0, 0))[1]
            for u in self.db.usage_by_provider()
        )
        return {
            "title": bible.get("title", self.cfg.book.title),
            "target_chapters": self.cfg.book.target_chapters,
            "planned": len(chapters),
            "final": len(finals),
            "words": sum(c["words"] for c in finals),
            "arcs": len(self.db.arcs()),
            "current": next((c["idx"] for c in chapters if c["status"] != "final"), None),
            "current_stage": next((c["status"] for c in chapters if c["status"] != "final"), None),
            "warnings": [{"idx": c["idx"], "warn": c["warn"]} for c in chapters if c["warn"]],
            "usage": self.db.usage_stats(),
            "cost": round(cost, 4),
        }
