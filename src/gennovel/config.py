"""配置加载：gennovel.yaml -> 类型化配置对象，带默认值合并。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ProviderCfg:
    name: str
    base_url: str
    model: str
    api_key: str = ""
    api_key_env: str = ""
    supports_json_mode: bool = True
    timeout: float = 300.0
    price_in: float = 0.0    # 输入价格（每 1M tokens，任意货币单位，用于成本核算）
    price_out: float = 0.0   # 输出价格（每 1M tokens）

    def resolve_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""


@dataclass
class StageCfg:
    model: str = ""          # 留空则用供应商默认模型
    provider: str = ""       # 留空则按 providers 顺序故障转移
    temperature: float = 0.7
    max_tokens: int = 8192
    json_mode: bool = False


# 各阶段默认参数：创作类高温、审校/抽取类低温 + JSON 模式
STAGE_DEFAULTS: dict[str, StageCfg] = {
    "bible":     StageCfg(temperature=0.8, max_tokens=8192, json_mode=True),
    "arcs":      StageCfg(temperature=0.8, max_tokens=4096, json_mode=True),
    "beats":     StageCfg(temperature=0.8, max_tokens=8192, json_mode=True),
    "draft":     StageCfg(temperature=0.9, max_tokens=12288),
    "check":     StageCfg(temperature=0.2, max_tokens=4096, json_mode=True),
    "revise":    StageCfg(temperature=0.7, max_tokens=12288),
    "deslop":    StageCfg(temperature=0.4, max_tokens=12288),
    "extract":   StageCfg(temperature=0.1, max_tokens=4096, json_mode=True),
    "summarize": StageCfg(temperature=0.3, max_tokens=2048, json_mode=True),
}


@dataclass
class BookCfg:
    title: str = "未命名作品"
    premise: str = ""
    genre: str = ""
    style: str = ""
    language: str = "zh"
    target_chapters: int = 30
    words_per_chapter: int = 3000


@dataclass
class EngineCfg:
    max_revise_rounds: int = 2
    batch_plan_chapters: int = 5
    context_budget_tokens: int = 24000
    long_summary_compress_every: int = 10
    min_avg_score: float = 7.0
    prev_tail_chars: int = 600
    retry_attempts: int = 4
    retry_base_delay: float = 2.0


@dataclass
class Config:
    book: BookCfg = field(default_factory=BookCfg)
    providers: list[ProviderCfg] = field(default_factory=list)
    stages: dict[str, StageCfg] = field(default_factory=dict)
    engine: EngineCfg = field(default_factory=EngineCfg)


def _merge_dc(cls, data: dict):
    known = {f for f in cls.__dataclass_fields__}
    return cls(**{k: v for k, v in (data or {}).items() if k in known})


def load_config(project_dir: str | Path) -> Config:
    path = Path(project_dir) / "gennovel.yaml"
    if not path.exists():
        raise FileNotFoundError(f"未找到配置文件: {path}（先运行 gennovel init）")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    providers = [_merge_dc(ProviderCfg, p) for p in raw.get("providers", [])]

    stages: dict[str, StageCfg] = {}
    raw_stages = raw.get("stages", {}) or {}
    for name, default in STAGE_DEFAULTS.items():
        user = raw_stages.get(name, {}) or {}
        known = {k: v for k, v in user.items() if k in StageCfg.__dataclass_fields__}
        stages[name] = StageCfg(**{**default.__dict__, **known})

    return Config(
        book=_merge_dc(BookCfg, raw.get("book", {})),
        providers=providers,
        stages=stages,
        engine=_merge_dc(EngineCfg, raw.get("engine", {})),
    )


DEFAULT_CONFIG_YAML = """\
# GenNovel 项目配置
book:
  title: "未命名作品"
  premise: >-
    （必填）一句到一段话的故事前提。例如：
    一个在废土世界靠修复旧时代机械为生的少女，捡到一台会说话的战争AI，
    被卷入各方势力对旧文明军火库的争夺。
  genre: "废土科幻"
  style: "第三人称限知视角，冷峻克制，重具体细节与动作，少抒情"
  language: zh
  target_chapters: 30
  words_per_chapter: 3000

# OpenAI Chat Completions 兼容供应商，按顺序故障转移
providers:
  - name: deepseek
    base_url: https://api.deepseek.com/v1
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    price_in: 2.0    # 可选：每 1M tokens 价格，填了就能在 status 里看到成本
    price_out: 8.0
  # - name: kimi
  #   base_url: https://api.moonshot.cn/v1
  #   model: kimi-k2-0711-preview
  #   api_key_env: MOONSHOT_API_KEY
  # - name: ollama
  #   base_url: http://localhost:11434/v1
  #   model: qwen3:32b
  #   supports_json_mode: false

# 可按阶段覆盖模型/温度（省钱技巧：摘要/抽取用便宜模型，规划/正文用强模型）
stages:
  draft:
    temperature: 0.9
  # summarize:
  #   provider: ollama

engine:
  max_revise_rounds: 2        # check 不通过时最多修订轮数
  batch_plan_chapters: 5      # 每次节拍规划的章数
  context_budget_tokens: 24000
  min_avg_score: 7.0          # check 平均分低于此值触发修订
"""
