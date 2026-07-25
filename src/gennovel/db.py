"""SQLite 持久层：WAL 模式、每阶段事务落库即 checkpoint、FTS 关键词召回。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .util import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS arcs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idx INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    chapters_estimate INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idx INTEGER NOT NULL UNIQUE,
    arc_idx INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    plan_json TEXT NOT NULL DEFAULT '{}',
    draft TEXT NOT NULL DEFAULT '',
    final TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    check_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'planned',
    revise_round INTEGER NOT NULL DEFAULT 0,
    warn TEXT NOT NULL DEFAULT '',
    words INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stage_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    chapter_idx INTEGER,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chapter_fts USING fts5(
    idx UNINDEXED, title, summary
);
"""

class DB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with self.conn:
            self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    @contextmanager
    def tx(self):
        """一个阶段 = 一个事务；崩溃时该阶段整体回滚，resume 从该阶段重跑。"""
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---- meta（bible / state / long_summary 等 JSON 文档） ----
    def get_meta(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_meta(self, key: str, value, conn: sqlite3.Connection | None = None):
        c = conn or self.conn
        c.execute(
            "INSERT INTO meta(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), now_iso()),
        )
        if conn is None:
            self.conn.commit()

    # ---- arcs ----
    def add_arc(self, idx: int, title: str, plan: dict, chapters_estimate: int, conn=None):
        c = conn or self.conn
        c.execute(
            "INSERT INTO arcs(idx,title,plan_json,chapters_estimate,created_at) VALUES(?,?,?,?,?)",
            (idx, title, json.dumps(plan, ensure_ascii=False), chapters_estimate, now_iso()),
        )
        if conn is None:
            self.conn.commit()

    def arcs(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM arcs ORDER BY idx").fetchall()
        return [dict(r) | {"plan": json.loads(r["plan_json"])} for r in rows]

    # ---- chapters ----
    def add_chapter(self, idx: int, arc_idx: int, title: str, plan: dict, conn=None):
        c = conn or self.conn
        c.execute(
            "INSERT INTO chapters(idx,arc_idx,title,plan_json,status,updated_at) VALUES(?,?,?,?,'planned',?)",
            (idx, arc_idx, title, json.dumps(plan, ensure_ascii=False), now_iso()),
        )
        if conn is None:
            self.conn.commit()

    def chapter(self, idx: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM chapters WHERE idx=?", (idx,)).fetchone()
        return self._ch(row) if row else None

    def chapters(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM chapters ORDER BY idx").fetchall()
        return [self._ch(r) for r in rows]

    @staticmethod
    def _ch(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["plan"] = json.loads(d.pop("plan_json") or "{}")
        d["check"] = json.loads(d.pop("check_json") or "{}")
        return d

    def update_chapter(self, idx: int, conn=None, **fields):
        c = conn or self.conn
        cols, vals = [], []
        for k, v in fields.items():
            if k in ("plan", "check"):
                k = {"plan": "plan_json", "check": "check_json"}[k]
                v = json.dumps(v, ensure_ascii=False)
            cols.append(f"{k}=?")
            vals.append(v)
        cols.append("updated_at=?")
        vals.append(now_iso())
        vals.append(idx)
        c.execute(f"UPDATE chapters SET {', '.join(cols)} WHERE idx=?", vals)
        if conn is None:
            self.conn.commit()

    def next_unfinished_chapter(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM chapters WHERE status!='final' ORDER BY idx LIMIT 1"
        ).fetchone()
        return self._ch(row) if row else None

    def count_chapters(self, arc_idx: int | None = None, final_only: bool = False) -> int:
        q = "SELECT COUNT(*) c FROM chapters WHERE 1=1"
        args: list = []
        if arc_idx is not None:
            q += " AND arc_idx=?"
            args.append(arc_idx)
        if final_only:
            q += " AND status='final'"
        return self.conn.execute(q, args).fetchone()["c"]

    # ---- fts / 召回 ----
    def index_chapter(self, idx: int, title: str, summary: str, conn=None):
        c = conn or self.conn
        c.execute("DELETE FROM chapter_fts WHERE idx=?", (idx,))
        c.execute("INSERT INTO chapter_fts(idx,title,summary) VALUES(?,?,?)", (idx, title, summary))
        if conn is None:
            self.conn.commit()

    def search_summaries(self, keywords: list[str], limit: int = 3,
                         exclude_recent_after: int | None = None) -> list[dict]:
        """按关键词在历史章节摘要里做 OR 召回（FTS5，含 CJK 时降级为 LIKE）。"""
        kws = [k for k in keywords if k and len(k) >= 2][:8]
        if not kws:
            return []
        try:
            query = " OR ".join('"' + k.replace('"', "") + '"' for k in kws)
            rows = self.conn.execute(
                "SELECT idx,title,summary FROM chapter_fts WHERE chapter_fts MATCH ? LIMIT ?",
                (query, limit * 3),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:  # FTS 默认分词器对中文不友好，降级 LIKE
            seen = set()
            rows = []
            for k in kws:
                for r in self.conn.execute(
                    "SELECT idx,title,summary FROM chapter_fts WHERE summary LIKE ? LIMIT ?",
                    (f"%{k}%", limit),
                ).fetchall():
                    if r["idx"] not in seen:
                        seen.add(r["idx"])
                        rows.append(r)
        out = [dict(r) for r in rows]
        if exclude_recent_after is not None:
            out = [r for r in out if r["idx"] < exclude_recent_after]
        return out[:limit]

    # ---- 审计与快照 ----
    def log_run(self, stage: str, chapter_idx, provider: str, model: str, status: str,
                prompt_tokens: int = 0, completion_tokens: int = 0, duration_ms: int = 0, error: str = ""):
        with self.conn:
            self.conn.execute(
                "INSERT INTO stage_runs(stage,chapter_idx,provider,model,status,prompt_tokens,"
                "completion_tokens,duration_ms,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (stage, chapter_idx, provider, model, status, prompt_tokens,
                 completion_tokens, duration_ms, error[:2000], now_iso()),
            )

    def snapshot(self, kind: str, ref: str, data, conn=None):
        c = conn or self.conn
        c.execute(
            "INSERT INTO snapshots(kind,ref,data,created_at) VALUES(?,?,?,?)",
            (kind, ref, json.dumps(data, ensure_ascii=False), now_iso()),
        )
        if conn is None:
            self.conn.commit()

    def usage_stats(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) calls, COALESCE(SUM(prompt_tokens),0) pt, "
            "COALESCE(SUM(completion_tokens),0) ct FROM stage_runs WHERE status='ok'"
        ).fetchone()
        errs = self.conn.execute(
            "SELECT COUNT(*) c FROM stage_runs WHERE status='error'"
        ).fetchone()["c"]
        return {"calls": row["calls"], "prompt_tokens": row["pt"],
                "completion_tokens": row["ct"], "errors": errs}

    def usage_by_provider(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT provider, COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct, "
            "COUNT(*) calls FROM stage_runs WHERE status='ok' GROUP BY provider"
        ).fetchall()
        return [dict(r) for r in rows]
