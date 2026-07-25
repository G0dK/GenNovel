"""通用工具：token 估算、健壮 JSON 提取、预算截断。"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


_CJK = re.compile(r"[　-〿㐀-䶿一-鿿豈-﫿＀-￯]")


def est_tokens(text: str) -> int:
    """粗略 token 估算：中文约 0.7 token/字，其余按空白分词约 1.3 token/词。"""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    rest = _CJK.sub(" ", text)
    words = len(rest.split())
    return int(cjk * 0.7 + words * 1.3) + 1


def truncate_to_budget(text: str, budget_tokens: int, head_ratio: float = 0.55) -> str:
    """超预算时保留头尾并插入显式压缩标记（头 55% / 尾 45%）。"""
    if est_tokens(text) <= budget_tokens:
        return text
    # 按字符近似换算预算
    ratio = budget_tokens / max(est_tokens(text), 1)
    keep_chars = int(len(text) * ratio)
    head = int(keep_chars * head_ratio)
    tail = keep_chars - head
    return text[:head] + "\n……[内容已按上下文预算压缩]……\n" + (text[-tail:] if tail > 0 else "")


def _scan_balanced(text: str, start: int) -> str | None:
    """从 start（'{' 或 '['）开始用括号栈扫描出一段平衡的 JSON 候选。"""
    stack: list[str] = []
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            stack.append(c)
        elif c in "}]":
            if not stack:
                return None
            opener = stack.pop()
            if (opener == "{" and c != "}") or (opener == "[" and c != "]"):
                return None
            if not stack:
                return text[start : i + 1]
    return None


def extract_json(text: str):
    """从 LLM 输出中提取第一个可解析的 JSON 对象/数组。

    容忍 ```json 围栏、<json> 标签、前后闲话。解析失败返回 None。
    """
    if not text:
        return None
    # 优先取围栏/标签内内容
    for pat in (r"<json>(.*?)</json>", r"```json\s*(.*?)```", r"```\s*(.*?)```"):
        m = re.search(pat, text, re.DOTALL)
        if m:
            inner = m.group(1).strip()
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                text = inner + "\n" + text  # 围栏内容也参与后续扫描
                break
    for i, c in enumerate(text):
        if c in "{[":
            candidate = _scan_balanced(text, i)
            if candidate:
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
    return None


def strip_fences(text: str) -> str:
    """去掉整体包裹的 markdown 代码围栏（LLM 输出正文时偶尔会加）。"""
    t = text.strip()
    m = re.fullmatch(r"```[a-zA-Z]*\n(.*)\n```", t, re.DOTALL)
    return m.group(1).strip() if m else t
