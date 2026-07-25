"""LLM 客户端：重试、故障转移、json_mode 400 回退、Retry-After（httpx.MockTransport 离线验证）。"""
import json

import httpx
import pytest

from gennovel.config import STAGE_DEFAULTS, Config, EngineCfg, ProviderCfg, StageCfg
from gennovel.llm import LLMClient, LLMError


def make_cfg(providers: list[ProviderCfg]) -> Config:
    cfg = Config(providers=providers, stages=dict(STAGE_DEFAULTS),
                 engine=EngineCfg(retry_attempts=2, retry_base_delay=0))
    return cfg


def ok_response(text="pong"):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })


def test_failover_to_second_provider():
    calls = {"p1": 0, "p2": 0}

    def handler(request: httpx.Request):
        host = request.url.host
        calls[host.split(".")[0]] += 1
        if host.startswith("p1"):
            return httpx.Response(500, text="boom")
        return ok_response("来自p2")

    cfg = make_cfg([
        ProviderCfg("p1", "http://p1.test/v1", "m1"),
        ProviderCfg("p2", "http://p2.test/v1", "m2"),
    ])
    client = LLMClient(cfg, transport=httpx.MockTransport(handler))
    assert client.chat("draft", "s", "u") == "来自p2"
    assert calls["p1"] == 2  # 重试 2 次后转移
    assert calls["p2"] == 1


def test_non_retryable_4xx_fails_fast_then_failover():
    calls = {"p1": 0, "p2": 0}

    def handler(request: httpx.Request):
        name = request.url.host.split(".")[0]
        calls[name] += 1
        if name == "p1":
            return httpx.Response(403, text="forbidden")
        return ok_response()

    cfg = make_cfg([
        ProviderCfg("p1", "http://p1.test/v1", "m1"),
        ProviderCfg("p2", "http://p2.test/v1", "m2"),
    ])
    client = LLMClient(cfg, transport=httpx.MockTransport(handler))
    assert client.chat("draft", "s", "u") == "pong"
    assert calls["p1"] == 1  # 403 不重试，直接转移


def test_json_mode_400_fallback():
    seen_bodies = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen_bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, text="response_format not supported")
        return ok_response('{"ok": true}')

    cfg = make_cfg([ProviderCfg("p1", "http://p1.test/v1", "m1")])
    client = LLMClient(cfg, transport=httpx.MockTransport(handler))
    out = client.chat("check", "s", "u")  # check 默认 json_mode=True
    assert json.loads(out) == {"ok": True}
    assert "response_format" in seen_bodies[0]
    assert "response_format" not in seen_bodies[1]


def test_retry_on_429_with_retry_after():
    count = {"n": 0}

    def handler(request: httpx.Request):
        count["n"] += 1
        if count["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="rate limited")
        return ok_response()

    cfg = make_cfg([ProviderCfg("p1", "http://p1.test/v1", "m1")])
    client = LLMClient(cfg, transport=httpx.MockTransport(handler))
    assert client.chat("draft", "s", "u") == "pong"
    assert count["n"] == 2


def test_all_providers_fail():
    def handler(request: httpx.Request):
        return httpx.Response(503, text="down")

    cfg = make_cfg([ProviderCfg("p1", "http://p1.test/v1", "m1")])
    client = LLMClient(cfg, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="所有供应商均失败"):
        client.chat("draft", "s", "u")


def test_empty_content_is_error():
    def handler(request: httpx.Request):
        return ok_response("")

    cfg = make_cfg([ProviderCfg("p1", "http://p1.test/v1", "m1")])
    client = LLMClient(cfg, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError):
        client.chat("draft", "s", "u")


def test_ping():
    def handler(request: httpx.Request):
        return ok_response("pong")

    cfg = make_cfg([ProviderCfg("p1", "http://p1.test/v1", "m1")])
    client = LLMClient(cfg, transport=httpx.MockTransport(handler))
    ok, ms, err = client.ping(cfg.providers[0])
    assert ok and err == ""


def test_stage_provider_preference():
    def handler(request: httpx.Request):
        return ok_response(request.url.host)

    cfg = make_cfg([
        ProviderCfg("cheap", "http://cheap.test/v1", "m1"),
        ProviderCfg("strong", "http://strong.test/v1", "m2"),
    ])
    cfg.stages["draft"] = StageCfg(provider="strong")
    client = LLMClient(cfg, transport=httpx.MockTransport(handler))
    assert client.chat("draft", "s", "u") == "strong.test"
