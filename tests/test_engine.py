"""全流程冒烟测试：FakeLLM 走完 设定集->弧->节拍->draft->check->revise->deslop->extract->summarize。"""
import json

import pytest

from gennovel.engine import Engine
from gennovel.export import export_book

CONFIG = """\
book:
  title: "测试书"
  premise: "少女与会说话的战争AI在废土寻找军火库。"
  genre: "废土科幻"
  style: "冷峻克制"
  target_chapters: 2
  words_per_chapter: 500
providers:
  - name: fake
    base_url: http://fake.local/v1
    model: fake-model
engine:
  max_revise_rounds: 2
  batch_plan_chapters: 2
"""

BIBLE = {
    "title": "废土回声", "logline": "少女带着AI找军火库。", "themes": ["信任"],
    "style_card": {"pov": "第三人称限知", "tone": "冷峻", "prose_notes": "短句"},
    "world": [{"name": "灰港", "category": "地理", "desc": "废土集镇"}],
    "characters": [
        {"name": "阿椿", "role": "protagonist", "desc": "机械修理工", "voice": "话少",
         "flaw": "不信人", "goal": "活下去", "secret": "父亲身份", "status": "健康", "location": "灰港"},
        {"name": "零七", "role": "support", "desc": "战争AI", "voice": "书面语", "flaw": "记忆残缺",
         "goal": "找到军火库", "secret": "毁灭指令", "status": "运行", "location": "阿椿背包"},
    ],
    "locked_facts": ["零七不能直接杀人"],
    "ending_direction": "军火库被毁。",
}

ARC = {
    "title": "灰港风波", "goal": "离开灰港", "conflict": "拾荒帮追捕",
    "turning_points": ["身份暴露"], "chapters_estimate": 2,
    "entry_state": "灰港", "exit_state": "踏上旅途", "foreshadow_plan": ["父亲的地图"],
}

BEATS = {
    "chapters": [
        {"idx": 1, "title": "拾荒者", "units": [{"goal": "修好零七", "conflict": "零件短缺",
         "outcome": "修好", "change": "零七开口说话"}],
         "beats": ["阿椿在灰港市集找零件", "零七第一次开口"], "hook": "门外传来脚步声", "words_target": 500},
        {"idx": 2, "title": "追捕", "units": [{"goal": "逃出灰港", "conflict": "拾荒帮堵截",
         "outcome": "逃脱", "change": "阿椿受伤"}],
         "beats": ["拾荒帮破门", "阿椿带零七跳窗逃走"], "hook": "地图从怀里掉出", "words_target": 500},
    ]
}

DRAFT = "灰港的风带着铁锈味。" + "阿椿蹲在摊位前翻拣零件，指尖被割了道口子也没停。" * 20
CHECK_FAIL = {
    "issues": [{"category": "outline", "severity": 3, "desc": "缺少钩子",
                "evidence": "灰港的风带着铁锈味。", "fix_hint": "补上脚步声结尾"}],
    "scores": {"plot": 6, "character": 7, "prose": 7, "hook": 4}, "verdict": "revise",
}
CHECK_PASS = {"issues": [], "scores": {"plot": 8, "character": 8, "prose": 8, "hook": 8}, "verdict": "pass"}
REVISED = DRAFT + "门外传来了脚步声。"
EXTRACT = {
    "character_updates": [{"name": "阿椿", "is_new": False,
                           "changes": {"location": "市集"}, "evidence": "阿椿蹲在摊位前"}],
    "world_updates": [],
    "foreshadows_planted": [{"desc": "父亲的地图", "evidence": "地图从怀里掉出"}],
    "foreshadows_resolved": [], "timeline_events": [{"event": "零七首次开口", "evidence": "零七第一次开口"}],
    "uncertainties": [],
}
SUMMARY = {"summary": "阿椿修好零七，拾荒帮找上门。", "key_events": ["零七开口", "拾荒帮破门"]}


class FakeLLM:
    """按阶段返回预制输出；check 阶段第一次返回 revise 以覆盖修订循环。"""

    def __init__(self):
        self.calls = []
        self.check_count = 0

    def chat(self, stage, system, user, chapter_idx=None):
        self.calls.append(stage)
        if stage == "bible":
            return json.dumps(BIBLE, ensure_ascii=False)
        if stage == "arcs":
            return json.dumps(ARC, ensure_ascii=False)
        if stage == "beats":
            return json.dumps(BEATS, ensure_ascii=False)
        if stage == "draft":
            return DRAFT
        if stage == "check":
            self.check_count += 1
            return json.dumps(CHECK_FAIL if self.check_count == 1 else CHECK_PASS, ensure_ascii=False)
        if stage == "revise":
            return REVISED
        if stage == "deslop":
            return REVISED if "脚步声" in user else DRAFT
        if stage == "extract":
            return json.dumps(EXTRACT, ensure_ascii=False)
        if stage == "summarize":
            return json.dumps(SUMMARY, ensure_ascii=False)
        raise AssertionError(f"未知阶段 {stage}")


@pytest.fixture
def project(tmp_path):
    (tmp_path / "gennovel.yaml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


def test_full_pipeline(project):
    fake = FakeLLM()
    eng = Engine(project, llm=fake)
    try:
        done = eng.run(2)
        assert done == 2

        s = eng.status()
        assert s["final"] == 2 and s["planned"] == 2
        assert s["title"] == "废土回声"

        ch1 = eng.db.chapter(1)
        assert ch1["status"] == "final"
        assert ch1["revise_round"] == 1  # 第一轮 check 不过 -> 修订一次
        assert "脚步声" in ch1["final"]
        assert ch1["summary"].startswith("阿椿修好零七")

        state = eng.db.get_meta("state")
        assert any(f["desc"] == "父亲的地图" for f in state["foreshadows"])
        assert any(c["location"] == "市集" for c in state["characters"] if c["name"] == "阿椿")
        assert eng.db.get_meta("long_summary")

        # 幂等：目标已达成，再跑不再新增
        assert eng.run(1) == 0

        out = export_book(eng.db, project / "book.md", "md")
        text = out.read_text(encoding="utf-8")
        assert "# 废土回声" in text and "### 第1章 拾荒者" in text
    finally:
        eng.close()


def test_resume_from_checkpoint(project):
    """draft 后崩溃（check 抛错），重建引擎后 resume 从 check 续跑，草稿不重算。"""

    class CrashLLM(FakeLLM):
        def chat(self, stage, system, user, chapter_idx=None):
            if stage == "check" and self.check_count == 0:
                self.check_count += 1
                raise RuntimeError("模拟网络中断")
            return super().chat(stage, system, user, chapter_idx)

    crash = CrashLLM()
    eng = Engine(project, llm=crash)
    with pytest.raises(RuntimeError):
        eng.run(1)
    ch = eng.db.chapter(1)
    assert ch["status"] == "check" and ch["draft"]  # 草稿已 checkpoint
    eng.close()

    eng2 = Engine(project, llm=FakeLLM())
    try:
        assert eng2.run(1) == 1
        ch = eng2.db.chapter(1)
        assert ch["status"] == "final"
        assert "draft" not in eng2.llm.calls  # 第1章草稿未重算，从 check 断点续跑
    finally:
        eng2.close()


def test_redo(project):
    eng = Engine(project, llm=FakeLLM())
    try:
        eng.run(1)
        eng.redo(1, "planned")
        ch = eng.db.chapter(1)
        assert ch["status"] == "planned" and ch["draft"] == ""
    finally:
        eng.close()


def test_graceful_stop_at_stage_boundary(project):
    """draft 完成后请求停止：停在阶段边界，草稿已落库，续跑可达 final。"""

    class StopAfterDraft(FakeLLM):
        def __init__(self, engine_ref):
            super().__init__()
            self.engine_ref = engine_ref

        def chat(self, stage, system, user, chapter_idx=None):
            out = super().chat(stage, system, user, chapter_idx)
            if stage == "draft":
                self.engine_ref[0].request_stop()
            return out

    ref = []
    fake = StopAfterDraft(ref)
    eng = Engine(project, llm=fake)
    ref.append(eng)
    try:
        done = eng.run(2)
        assert done == 0  # 未完成任何整章
        ch = eng.db.chapter(1)
        assert ch["status"] == "check" and ch["draft"]  # 草稿保住，停在边界
    finally:
        eng.close()

    eng2 = Engine(project, llm=FakeLLM())
    try:
        assert eng2.run(2) == 2
    finally:
        eng2.close()


def test_backup(project):
    import zipfile

    from gennovel.export import backup_project

    eng = Engine(project, llm=FakeLLM())
    try:
        eng.run(1)
    finally:
        eng.close()
    out = backup_project(project)
    names = zipfile.ZipFile(out).namelist()
    assert "book.db" in names and "gennovel.yaml" in names
