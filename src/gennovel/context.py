"""上下文打包：为 draft 等阶段构建预算化的提示词变量。

策略（来自 Scriverse / OpenFic 的调研结论）：
- 结构化状态优先于原文；正文只带上一章结尾片段
- 关键词召回历史摘要（FTS/LIKE），不引入向量库
- 超预算时按 头55%/尾45% 截断长摘要并插入显式压缩标记
"""
from __future__ import annotations

import json

from .config import Config
from .db import DB
from .state import character_cards, open_foreshadows, world_block
from .util import est_tokens, truncate_to_budget


def bible_core(bible: dict, include_ending: bool = False) -> str:
    parts = [
        f"书名：{bible.get('title', '')}",
        f"一句话故事：{bible.get('logline', '')}",
        f"主题：{'、'.join(bible.get('themes', []))}",
        f"文风卡：{json.dumps(bible.get('style_card', {}), ensure_ascii=False)}",
    ]
    if include_ending:
        parts.append(f"结局方向：{bible.get('ending_direction', '')}")
    return "\n".join(parts)


def locked_facts(bible: dict) -> str:
    facts = bible.get("locked_facts", [])
    return "\n".join(f"- {f}" for f in facts) or "（无）"


def format_units(plan: dict) -> str:
    out = []
    for i, u in enumerate(plan.get("units", []), 1):
        out.append(f"{i}. 目标: {u.get('goal','')}｜冲突: {u.get('conflict','')}"
                   f"｜结果: {u.get('outcome','')}｜变化: {u.get('change','')}")
    return "\n".join(out) or "（无）"


def format_beats(plan: dict) -> str:
    return "\n".join(f"{i}. {b}" for i, b in enumerate(plan.get("beats", []), 1)) or "（无）"


def recent_summaries(db: DB, before_idx: int, n: int = 3) -> str:
    chs = [c for c in db.chapters() if c["idx"] < before_idx and c["summary"]]
    out = [f"第{c['idx']}章《{c['title']}》：{c['summary']}" for c in chs[-n:]]
    return "\n".join(out) or "（这是第一章，没有前文）"


def related_summaries(db: DB, state: dict, plan_text: str, before_idx: int, n_recent: int = 3) -> str:
    """用出现在本章计划里的角色名/设定名作关键词，召回更早章节的摘要。"""
    keywords = [c["name"] for c in state["characters"] if c["name"] and c["name"] in plan_text]
    keywords += [w["name"] for w in state["world"] if w["name"] and w["name"] in plan_text]
    rows = db.search_summaries(keywords, limit=3, exclude_recent_after=before_idx - n_recent)
    return "\n".join(f"第{r['idx']}章《{r['title']}》：{r['summary']}" for r in rows) or "（无）"


def active_character_names(state: dict, plan_text: str) -> list[str]:
    return [c["name"] for c in state["characters"] if c["name"] and c["name"] in plan_text]


def build_draft_vars(cfg: Config, db: DB, bible: dict, state: dict,
                     ch: dict, arc: dict, prose_rules: str) -> dict:
    plan = ch["plan"]
    plan_text = json.dumps(plan, ensure_ascii=False)
    long_summary = db.get_meta("long_summary", "")
    prev = db.chapter(ch["idx"] - 1)
    prev_tail = (prev["final"] or prev["draft"])[-cfg.engine.prev_tail_chars:] if prev else "（这是第一章）"

    variables = {
        "bible_core": bible_core(bible),
        "locked_facts": locked_facts(bible),
        "active_characters": character_cards(state, active_character_names(state, plan_text)),
        "world_entries": world_block(state, plan_text),
        "open_foreshadows": open_foreshadows(state),
        "long_summary": long_summary or "（无）",
        "related_summaries": related_summaries(db, state, plan_text, ch["idx"]),
        "recent_summaries": recent_summaries(db, ch["idx"]),
        "prev_tail": prev_tail,
        "arc_brief": (f"第{arc['idx']}弧《{arc['title']}》：{arc['plan'].get('goal','')}"
                      f"｜冲突：{arc['plan'].get('conflict','')}"),
        "idx": ch["idx"],
        "title": ch["title"],
        "units": format_units(plan),
        "beats": format_beats(plan),
        "hook": plan.get("hook", ""),
        "words_target": plan.get("words_target", cfg.book.words_per_chapter),
        "prose_rules": prose_rules,
    }

    # 预算控制：优先压缩长摘要与相关摘要（结构化状态和本章计划是刚需，不压）
    budget = cfg.engine.context_budget_tokens
    total = sum(est_tokens(str(v)) for v in variables.values())
    if total > budget:
        overflow_ratio = budget / total
        for key in ("long_summary", "related_summaries", "prev_tail"):
            cur = str(variables[key])
            variables[key] = truncate_to_budget(cur, max(int(est_tokens(cur) * overflow_ratio), 200))
    return variables
