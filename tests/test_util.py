from gennovel.util import est_tokens, extract_json, strip_fences, truncate_to_budget


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_chatter_and_fence():
    text = '好的，以下是结果：\n```json\n{"title": "试", "n": [1,2]}\n```\n希望有帮助'
    assert extract_json(text) == {"title": "试", "n": [1, 2]}


def test_extract_json_nested_and_strings_with_braces():
    text = '前言 {"a": {"b": "含}花括号{的字符串"}, "c": [1, {"d": 2}]} 后记'
    assert extract_json(text) == {"a": {"b": "含}花括号{的字符串"}, "c": [1, {"d": 2}]}


def test_extract_json_invalid():
    assert extract_json("完全没有 JSON") is None
    assert extract_json("{broken: ") is None


def test_strip_fences():
    assert strip_fences("```\n正文内容\n```") == "正文内容"
    assert strip_fences("正文内容") == "正文内容"


def test_est_tokens_cjk():
    assert est_tokens("你好世界") >= 2
    assert est_tokens("") == 0


def test_truncate_to_budget():
    text = "长" * 10000
    out = truncate_to_budget(text, 100)
    assert len(out) < len(text)
    assert "内容已按上下文预算压缩" in out
    assert truncate_to_budget("短文本", 1000) == "短文本"
