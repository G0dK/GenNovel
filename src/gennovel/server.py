"""FastAPI 服务：Web 监控面板 + API。服务器部署入口。

并发模型刻意简单：同一项目同时只允许一个写作任务（RunManager 单线程 worker），
请求处理各自开短连接读库。SQLite WAL 下读写互不阻塞。

安全：设置环境变量 GENNOVEL_TOKEN 后，/api/* 全部要求 X-Api-Token 请求头
（或 ?token= 查询参数）。/healthz 与静态页始终开放。
"""
from __future__ import annotations

import os
import threading
from collections import deque
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .db import DB
from .util import now_iso

STATIC_DIR = Path(__file__).parent / "static"


class RunRequest(BaseModel):
    chapters: int = 1


class RunManager:
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.engine = None  # 运行中的 Engine，用于优雅停止
        self.log: deque[str] = deque(maxlen=200)
        self.last_error: str = ""

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, chapters: int) -> bool:
        with self.lock:
            if self.running:
                return False
            self.last_error = ""
            self.thread = threading.Thread(target=self._work, args=(chapters,), daemon=True)
            self.thread.start()
            return True

    def request_stop(self) -> bool:
        with self.lock:
            if not self.running or self.engine is None:
                return False
            self.engine.request_stop()
            self._emit("收到停止请求，将在当前阶段完成后停止")
            return True

    def _work(self, chapters: int):
        from .engine import Engine
        self._emit(f"任务开始：推进 {chapters} 章")
        eng = None
        try:
            eng = Engine(self.project_dir, on_event=self._emit)
            self.engine = eng
            done = eng.run(chapters)
            self._emit(f"任务结束：完成 {done} 章")
        except Exception as e:  # 任务失败不打死进程，状态已 checkpoint，可重跑
            self.last_error = str(e)
            self._emit(f"任务失败: {e}（已保留断点，可直接重试）")
        finally:
            self.engine = None
            if eng:
                eng.close()

    def _emit(self, msg: str):
        self.log.append(f"[{now_iso()}] {msg}")


def create_app(project_dir: str | Path, token: str | None = None) -> FastAPI:
    project_dir = Path(project_dir)
    # 幂等自举：目录未初始化时自动生成配置与提示词（Docker 首启免手工 init）
    project_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = project_dir / "gennovel.yaml"
    if not cfg_path.exists():
        from .config import DEFAULT_CONFIG_YAML
        cfg_path.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
    from .prompts import PromptLoader
    PromptLoader(project_dir).copy_defaults_to(project_dir / "prompts")

    api_token = token if token is not None else os.environ.get("GENNOVEL_TOKEN", "")
    app = FastAPI(title="GenNovel", docs_url="/api/docs")
    manager = RunManager(project_dir)

    async def require_token(request: Request):
        if not api_token:
            return
        supplied = request.headers.get("x-api-token") or request.query_params.get("token")
        if supplied != api_token:
            raise HTTPException(401, "无效或缺失 API Token（X-Api-Token 头或 ?token=）")

    api = APIRouter(prefix="/api", dependencies=[])
    from fastapi import Depends
    api.dependencies.append(Depends(require_token))

    def _db() -> DB:
        return DB(project_dir / "book.db")

    @app.get("/healthz")
    def healthz():
        ok = (project_dir / "gennovel.yaml").exists()
        return {"ok": ok, "running": manager.running, "auth": bool(api_token)}

    @api.get("/status")
    def status():
        from .engine import Engine
        eng = Engine(project_dir)
        try:
            s = eng.status()
        finally:
            eng.close()
        s["running"] = manager.running
        s["last_error"] = manager.last_error
        return s

    @api.get("/chapters")
    def chapters():
        db = _db()
        try:
            return [
                {"idx": c["idx"], "title": c["title"], "status": c["status"],
                 "words": c["words"], "summary": c["summary"], "warn": c["warn"],
                 "arc_idx": c["arc_idx"]}
                for c in db.chapters()
            ]
        finally:
            db.close()

    @api.get("/chapters/{idx}")
    def chapter(idx: int):
        db = _db()
        try:
            c = db.chapter(idx)
            if not c:
                raise HTTPException(404, "章节不存在")
            return c
        finally:
            db.close()

    @api.get("/state")
    def state():
        db = _db()
        try:
            return {"bible": db.get_meta("bible"), "state": db.get_meta("state"),
                    "long_summary": db.get_meta("long_summary", "")}
        finally:
            db.close()

    @api.get("/log")
    def log():
        return {"lines": list(manager.log)}

    @api.post("/run")
    def run(req: RunRequest):
        if not manager.start(max(1, min(req.chapters, 500))):
            raise HTTPException(409, "已有任务在运行")
        return {"started": True}

    @api.post("/stop")
    def stop():
        return {"stopping": manager.request_stop()}

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    app.include_router(api)
    return app
