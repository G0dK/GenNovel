"""服务端：自举、鉴权、端点行为。"""
import pytest
from fastapi.testclient import TestClient

from gennovel.server import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "proj", token=""))


def test_auto_init_and_healthz(tmp_path):
    d = tmp_path / "proj"
    c = TestClient(create_app(d, token=""))
    assert (d / "gennovel.yaml").exists()
    assert (d / "prompts" / "draft.md").exists()
    r = c.get("/healthz").json()
    assert r["ok"] is True and r["auth"] is False


def test_status_and_chapters_empty(client):
    s = client.get("/api/status").json()
    assert s["final"] == 0 and s["running"] is False
    assert client.get("/api/chapters").json() == []
    assert client.get("/api/chapters/1").status_code == 404


def test_dashboard_served(client):
    html = client.get("/").text
    assert "GenNovel" in html and "fetchJ" in html


def test_token_auth(tmp_path):
    c = TestClient(create_app(tmp_path / "proj", token="secret"))
    assert c.get("/healthz").json()["auth"] is True      # healthz 不需要 token
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"X-Api-Token": "wrong"}).status_code == 401
    assert c.get("/api/status", headers={"X-Api-Token": "secret"}).status_code == 200
    assert c.get("/api/status", params={"token": "secret"}).status_code == 200


def test_stop_when_idle(client):
    assert client.post("/api/stop").json() == {"stopping": False}


def test_run_fails_gracefully_without_premise(client):
    # 默认配置 premise 是占位符 -> 任务快速失败但进程存活，错误可见
    assert client.post("/api/run", json={"chapters": 1}).json() == {"started": True}
    import time
    for _ in range(50):
        s = client.get("/api/status").json()
        if not s["running"]:
            break
        time.sleep(0.1)
    assert "premise" in s["last_error"]
    assert any("任务失败" in line for line in client.get("/api/log").json()["lines"])
