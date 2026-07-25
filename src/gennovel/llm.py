"""LLM 客户端：OpenAI Chat Completions 兼容，多供应商顺序故障转移 + 指数退避重试 + 审计。

不依赖任何 SDK/框架，一个 httpx 走天下（DeepSeek/Kimi/OpenAI/OpenRouter/Ollama 等均兼容）。
可注入 httpx transport 以便离线测试。
"""
from __future__ import annotations

import time

import httpx

from .config import Config, ProviderCfg, StageCfg
from .db import DB
from .util import est_tokens

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 60.0


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, cfg: Config, db: DB | None = None,
                 transport: httpx.BaseTransport | None = None):
        if not cfg.providers:
            raise LLMError("配置中没有任何 provider（gennovel.yaml -> providers）")
        self.cfg = cfg
        self.db = db
        self.transport = transport

    # 供测试替换的单点
    def chat(self, stage: str, system: str, user: str, chapter_idx: int | None = None) -> str:
        scfg = self.cfg.stages[stage]
        providers = self._provider_order(scfg)
        last_err: Exception | None = None
        for p in providers:
            try:
                return self._call_with_retry(p, scfg, stage, system, user, chapter_idx)
            except LLMError as e:
                last_err = e
                continue  # 故障转移到下一供应商
        raise LLMError(f"[{stage}] 所有供应商均失败: {last_err}")

    def ping(self, p: ProviderCfg) -> tuple[bool, int, str]:
        """连通性诊断：发一次最小对话请求，返回 (是否成功, 延迟ms, 错误信息)。"""
        t0 = time.monotonic()
        try:
            body = {"model": p.model,
                    "messages": [{"role": "user", "content": "ping，请回复 pong"}],
                    "max_tokens": 8, "stream": False}
            headers = {"Content-Type": "application/json"}
            if key := p.resolve_key():
                headers["Authorization"] = f"Bearer {key}"
            with httpx.Client(timeout=30, transport=self.transport) as client:
                r = client.post(p.base_url.rstrip("/") + "/chat/completions",
                                json=body, headers=headers)
                r.raise_for_status()
                r.json()["choices"][0]["message"]
            return True, int((time.monotonic() - t0) * 1000), ""
        except Exception as e:
            return False, int((time.monotonic() - t0) * 1000), str(e)[:200]

    def _provider_order(self, scfg: StageCfg) -> list[ProviderCfg]:
        if scfg.provider:
            named = [p for p in self.cfg.providers if p.name == scfg.provider]
            rest = [p for p in self.cfg.providers if p.name != scfg.provider]
            return named + rest
        return list(self.cfg.providers)

    @staticmethod
    def _retry_delay(resp: httpx.Response | None, fallback: float) -> float:
        """429 优先遵循 Retry-After 头，其余用指数退避。"""
        if resp is not None and resp.status_code == 429:
            ra = resp.headers.get("retry-after", "")
            try:
                return min(float(ra), MAX_RETRY_AFTER)
            except ValueError:
                pass
        return fallback

    def _call_with_retry(self, p: ProviderCfg, scfg: StageCfg, stage: str,
                         system: str, user: str, chapter_idx) -> str:
        attempts = self.cfg.engine.retry_attempts
        base = self.cfg.engine.retry_base_delay
        json_mode = scfg.json_mode and p.supports_json_mode
        last: Exception | None = None
        for i in range(attempts):
            delay = base * (2 ** i)
            try:
                return self._call_once(p, scfg, stage, system, user, chapter_idx, json_mode)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                # 部分兼容端点不认 response_format：去掉后立刻重试一次
                if code == 400 and json_mode:
                    json_mode = False
                    last = e
                    continue
                if code not in RETRYABLE_STATUS:
                    self._log(stage, chapter_idx, p, scfg, "error",
                              err=f"HTTP {code}: {e.response.text[:300]}")
                    raise LLMError(f"{p.name} HTTP {code}") from e
                last = e
                delay = self._retry_delay(e.response, delay)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = e
            if i < attempts - 1:
                time.sleep(delay)
        self._log(stage, chapter_idx, p, scfg, "error", err=str(last)[:300])
        raise LLMError(f"{p.name} 重试 {attempts} 次后仍失败: {last}")

    def _call_once(self, p: ProviderCfg, scfg: StageCfg, stage: str,
                   system: str, user: str, chapter_idx, json_mode: bool) -> str:
        model = scfg.model or p.model
        body: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": scfg.temperature,
            "max_tokens": scfg.max_tokens,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if key := p.resolve_key():
            headers["Authorization"] = f"Bearer {key}"
        url = p.base_url.rstrip("/") + "/chat/completions"

        t0 = time.monotonic()
        with httpx.Client(timeout=p.timeout, transport=self.transport) as client:
            resp = client.post(url, json=body, headers=headers)
            resp.raise_for_status()
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise LLMError(f"{p.name} 响应格式异常: {str(data)[:300]}") from e
        usage = data.get("usage") or {}
        self._log(
            stage, chapter_idx, p, scfg, "ok",
            pt=usage.get("prompt_tokens", est_tokens(system + user)),
            ct=usage.get("completion_tokens", est_tokens(content)),
            ms=int((time.monotonic() - t0) * 1000),
        )
        if not content.strip():
            raise LLMError(f"{p.name} 返回空内容")
        return content

    def _log(self, stage, chapter_idx, p: ProviderCfg, scfg: StageCfg, status,
             pt=0, ct=0, ms=0, err=""):
        if self.db:
            self.db.log_run(stage, chapter_idx, p.name, scfg.model or p.model,
                            status, pt, ct, ms, err)
