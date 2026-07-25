"""故事状态：角色 / 世界观 / 伏笔 / 时间线 的结构化事实层。

原则（来自 Scriverse 的审计式设计）：状态更新来自 extract 阶段带证据的抽取，
应用前先快照，可疑项进 uncertainties 交人工确认，绝不静默合并角色。
"""
from __future__ import annotations


def init_state_from_bible(bible: dict) -> dict:
    chars = []
    for c in bible.get("characters", []):
        chars.append({
            "name": c.get("name", ""),
            "role": c.get("role", "support"),
            "desc": c.get("desc", ""),
            "voice": c.get("voice", ""),
            "flaw": c.get("flaw", ""),
            "goal": c.get("goal", ""),
            "secret": c.get("secret", ""),
            "status": c.get("status", ""),
            "location": c.get("location", ""),
            "notes": "",
        })
    world = [
        {"name": w.get("name", ""), "category": w.get("category", ""), "desc": w.get("desc", "")}
        for w in bible.get("world", [])
    ]
    return {
        "characters": chars,
        "world": world,
        "foreshadows": [],
        "timeline": [],
        "next_foreshadow_id": 1,
    }


def apply_extract(state: dict, ext: dict, chapter_idx: int) -> tuple[dict, list[str]]:
    """把 extract 阶段的增量更新应用到状态，返回 (新状态, 警告列表)。"""
    warns: list[str] = []
    by_name = {c["name"]: c for c in state["characters"]}

    for cu in ext.get("character_updates", []) or []:
        name = (cu.get("name") or "").strip()
        if not name:
            continue
        changes = cu.get("changes") or {}
        if name in by_name:
            ch = by_name[name]
            for k in ("status", "location", "goal", "notes"):
                if changes.get(k):
                    if k == "notes":
                        ch["notes"] = (ch["notes"] + f"\n[第{chapter_idx}章] {changes[k]}").strip()
                    else:
                        ch[k] = changes[k]
        elif cu.get("is_new"):
            state["characters"].append({
                "name": name, "role": "support",
                "desc": changes.get("notes", ""), "voice": "", "flaw": "", "goal": changes.get("goal", ""),
                "secret": "", "status": changes.get("status", ""),
                "location": changes.get("location", ""), "notes": f"[第{chapter_idx}章] 首次登场",
            })
            by_name[name] = state["characters"][-1]
        else:
            warns.append(f"角色更新指向不存在的角色「{name}」且未标记 is_new，已忽略")

    wnames = {w["name"]: w for w in state["world"]}
    for wu in ext.get("world_updates", []) or []:
        name = (wu.get("name") or "").strip()
        if not name or not wu.get("desc"):
            continue
        if name in wnames:
            wnames[name]["desc"] = (wnames[name]["desc"] + f"\n[第{chapter_idx}章] {wu['desc']}").strip()
        else:
            state["world"].append({"name": name, "category": "新增",
                                   "desc": f"[第{chapter_idx}章] {wu['desc']}"})
            wnames[name] = state["world"][-1]

    for fp in ext.get("foreshadows_planted", []) or []:
        if not fp.get("desc"):
            continue
        state["foreshadows"].append({
            "id": state["next_foreshadow_id"], "desc": fp["desc"],
            "planted_chapter": chapter_idx, "status": "open",
            "resolved_chapter": None, "how": "",
        })
        state["next_foreshadow_id"] += 1

    by_id = {f["id"]: f for f in state["foreshadows"]}
    for fr in ext.get("foreshadows_resolved", []) or []:
        fid = fr.get("id")
        f = by_id.get(fid)
        if f and f["status"] == "open":
            f["status"] = "resolved"
            f["resolved_chapter"] = chapter_idx
            f["how"] = fr.get("how", "")
        else:
            warns.append(f"伏笔回收指向无效 id={fid}，已忽略")

    for te in ext.get("timeline_events", []) or []:
        if te.get("event"):
            state["timeline"].append({"chapter": chapter_idx, "event": te["event"]})

    for u in ext.get("uncertainties", []) or []:
        warns.append(f"待作者确认: {u}")
    return state, warns


# ---- 渲染为提示词片段 ----

def brief(state: dict, max_timeline: int = 12) -> str:
    lines = ["角色："]
    for c in state["characters"]:
        seg = f"- {c['name']}（{c['role']}）状态: {c['status'] or '未知'}；位置: {c['location'] or '未知'}"
        if c.get("goal"):
            seg += f"；目标: {c['goal']}"
        lines.append(seg)
    lines.append("近期时间线：")
    for t in state["timeline"][-max_timeline:]:
        lines.append(f"- 第{t['chapter']}章: {t['event']}")
    return "\n".join(lines)


def character_cards(state: dict, names: list[str]) -> str:
    picked = [c for c in state["characters"] if c["name"] in names]
    if not picked:
        picked = [c for c in state["characters"] if c["role"] == "protagonist"] or state["characters"][:3]
    out = []
    for c in picked:
        out.append(
            f"### {c['name']}（{c['role']}）\n"
            f"身份: {c['desc']}\n说话方式: {c['voice']}\n缺陷: {c['flaw']}\n"
            f"目标: {c['goal']}\n秘密: {c['secret']}\n"
            f"当前状态: {c['status']}；当前位置: {c['location']}\n"
            + (f"近况: {c['notes'][-500:]}" if c["notes"] else "")
        )
    return "\n".join(out)


def world_block(state: dict, text_for_match: str, cap: int = 8) -> str:
    hits = [w for w in state["world"] if w["name"] and w["name"] in text_for_match]
    rest = [w for w in state["world"] if w not in hits]
    picked = (hits + rest)[:cap]
    return "\n".join(f"- {w['name']}（{w['category']}）: {w['desc']}" for w in picked) or "（无）"


def open_foreshadows(state: dict, cap: int = 12) -> str:
    fs = [f for f in state["foreshadows"] if f["status"] == "open"][:cap]
    return "\n".join(f"- #{f['id']} {f['desc']}（第{f['planted_chapter']}章埋设）" for f in fs) or "（无）"
