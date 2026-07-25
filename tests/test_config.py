"""配置加载：默认值合并、阶段覆盖、密钥解析。"""
import pytest

from gennovel.config import ProviderCfg, load_config

YAML = """\
book:
  premise: "测试前提"
  target_chapters: 5
providers:
  - name: a
    base_url: http://a.test/v1
    model: m-a
    price_in: 1.5
stages:
  draft:
    temperature: 1.1
    model: m-draft
engine:
  max_revise_rounds: 0
"""


def test_load_and_merge(tmp_path):
    (tmp_path / "gennovel.yaml").write_text(YAML, encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.book.target_chapters == 5
    assert cfg.book.words_per_chapter == 3000            # 默认值保留
    assert cfg.providers[0].price_in == 1.5
    assert cfg.stages["draft"].temperature == 1.1        # 用户覆盖
    assert cfg.stages["draft"].model == "m-draft"
    assert cfg.stages["check"].json_mode is True         # 阶段默认保留
    assert cfg.engine.max_revise_rounds == 0
    assert cfg.engine.retry_attempts == 4                # 引擎默认保留


def test_missing_config_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path)


def test_api_key_resolution(monkeypatch):
    p = ProviderCfg("x", "http://x/v1", "m", api_key_env="SF_TEST_KEY")
    assert p.resolve_key() == ""
    monkeypatch.setenv("SF_TEST_KEY", "sk-123")
    assert p.resolve_key() == "sk-123"
    assert ProviderCfg("y", "http://y/v1", "m", api_key="direct").resolve_key() == "direct"
