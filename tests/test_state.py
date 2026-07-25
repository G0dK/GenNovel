"""故事状态：抽取应用的边界行为（防幻觉设计的落地验证）。"""
from gennovel.state import apply_extract, brief, init_state_from_bible, open_foreshadows

BIBLE = {
    "characters": [{"name": "阿椿", "role": "protagonist", "desc": "修理工"}],
    "world": [{"name": "灰港", "category": "地理", "desc": "集镇"}],
}


def make_state():
    return init_state_from_bible(BIBLE)


def test_unknown_character_without_is_new_is_ignored_with_warning():
    state, warns = apply_extract(make_state(), {
        "character_updates": [{"name": "神秘人", "is_new": False,
                               "changes": {"status": "出现"}, "evidence": "x"}],
    }, 1)
    assert all(c["name"] != "神秘人" for c in state["characters"])
    assert any("神秘人" in w for w in warns)


def test_new_character_with_is_new_is_added():
    state, warns = apply_extract(make_state(), {
        "character_updates": [{"name": "老猫", "is_new": True,
                               "changes": {"location": "码头"}, "evidence": "x"}],
    }, 2)
    cat = next(c for c in state["characters"] if c["name"] == "老猫")
    assert cat["location"] == "码头" and not warns


def test_foreshadow_lifecycle():
    state, _ = apply_extract(make_state(), {
        "foreshadows_planted": [{"desc": "地图", "evidence": "x"}],
    }, 1)
    fid = state["foreshadows"][0]["id"]
    assert "地图" in open_foreshadows(state)

    state, warns = apply_extract(state, {
        "foreshadows_resolved": [{"id": fid, "how": "打开", "evidence": "y"}],
    }, 5)
    f = state["foreshadows"][0]
    assert f["status"] == "resolved" and f["resolved_chapter"] == 5
    assert open_foreshadows(state) == "（无）"
    assert not warns


def test_invalid_foreshadow_id_warns():
    _, warns = apply_extract(make_state(), {
        "foreshadows_resolved": [{"id": 999, "how": "?", "evidence": "x"}],
    }, 1)
    assert any("999" in w for w in warns)


def test_uncertainties_become_warnings():
    _, warns = apply_extract(make_state(), {"uncertainties": ["阿椿的父亲可能是舰长"]}, 1)
    assert any("待作者确认" in w for w in warns)


def test_brief_contains_timeline():
    state, _ = apply_extract(make_state(), {
        "timeline_events": [{"event": "零七开口", "evidence": "x"}],
    }, 3)
    text = brief(state)
    assert "第3章: 零七开口" in text and "阿椿" in text
