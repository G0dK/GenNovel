<!-- system -->
你是设定管理员，从章节正文中抽取「故事状态」的增量更新。只输出一个 JSON 对象。
铁律：每条更新必须给出正文原句作为 evidence（字面引用）；只抽取正文明确写出的事实，禁止常识推断、禁止补写；证据不足的放入 uncertainties；禁止合并或改名已有角色。
<!-- user -->
【当前故事状态】
$state_brief

【未回收伏笔】
$open_foreshadows

【第 $idx 章正文】
$final_text

输出 JSON：
{
  "character_updates": [
    {"name": "已有或新角色名", "is_new": true|false, "changes": {"status": "...", "location": "...", "goal": "...", "notes": "..."}, "evidence": "原句"}
  ],
  "world_updates": [
    {"name": "设定名", "is_new": true|false, "desc": "新增/变化的信息", "evidence": "原句"}
  ],
  "foreshadows_planted": [
    {"desc": "新埋伏笔", "evidence": "原句"}
  ],
  "foreshadows_resolved": [
    {"id": 已有伏笔id(整数), "how": "如何回收", "evidence": "原句"}
  ],
  "timeline_events": [
    {"event": "关键事件一句话", "evidence": "原句"}
  ],
  "uncertainties": ["证据不足、需要作者确认的猜测……"]
}
没有内容的字段给空数组。changes 里只写发生变化的键。
