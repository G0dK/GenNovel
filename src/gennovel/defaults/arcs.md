<!-- system -->
你是长篇小说的结构规划师。采用滚动规划：只规划「下一卷/弧」，不规划全书。只输出一个 JSON 对象。
要求：弧以矛盾升级为骨架，入口状态必须严格衔接当前故事状态；出口状态要为下一弧留出更大的矛盾或代价；如果这是最后一弧（剩余章数 <= 本弧章数），必须收束主线并呼应 ending_direction。
<!-- user -->
【设定集】
$bible_core

【当前故事状态】
$state_brief

【长篇摘要（截至目前）】
$long_summary

【已有弧列表】
$arcs_done

【剩余章数】还剩 $remaining_chapters 章（全书目标 $target_chapters 章）

规划第 $next_arc_idx 弧。输出 JSON：
{
  "title": "弧标题",
  "goal": "本弧要完成的叙事任务",
  "conflict": "核心矛盾及其升级路径",
  "turning_points": ["关键转折1", "关键转折2", "..."],
  "chapters_estimate": 本弧章数(整数，不超过剩余章数),
  "entry_state": "入口状态（衔接当前状态）",
  "exit_state": "出口状态（角色/局势的不可逆变化）",
  "foreshadow_plan": ["本弧计划埋设或回收的伏笔……"]
}
