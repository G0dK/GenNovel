"""导出与备份：Markdown / TXT 导出（只导 final 章节）、项目热备份。"""
from __future__ import annotations

import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from .db import DB


def export_book(db: DB, out_path: str | Path, fmt: str = "md") -> Path:
    out_path = Path(out_path)
    bible = db.get_meta("bible") or {}
    title = bible.get("title", "未命名作品")
    arcs = {a["idx"]: a for a in db.arcs()}
    finals = [c for c in db.chapters() if c["status"] == "final"]

    parts: list[str] = []
    if fmt == "md":
        parts.append(f"# {title}\n")
        if bible.get("logline"):
            parts.append(f"> {bible['logline']}\n")
        seen_arcs: set[int] = set()
        for c in finals:
            if c["arc_idx"] not in seen_arcs:
                seen_arcs.add(c["arc_idx"])
                arc = arcs.get(c["arc_idx"])
                if arc:
                    parts.append(f"\n## 第{arc['idx']}卷 {arc['title']}\n")
            parts.append(f"\n### 第{c['idx']}章 {c['title']}\n\n{c['final']}\n")
    else:
        parts.append(f"{title}\n\n")
        for c in finals:
            parts.append(f"\n第{c['idx']}章 {c['title']}\n\n{c['final']}\n")

    out_path.write_text("".join(parts), encoding="utf-8")
    return out_path


def backup_project(project_dir: str | Path, out_path: str | Path | None = None) -> Path:
    """热备份：SQLite backup API 拷贝数据库（WAL 下运行中也安全）+ 配置 + 提示词打成 zip。"""
    project_dir = Path(project_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(out_path) if out_path else project_dir / f"backup-{stamp}.zip"

    db_file = project_dir / "book.db"
    tmp_db = project_dir / f".backup-{stamp}.db"
    try:
        if db_file.exists():
            src = sqlite3.connect(db_file)
            dst = sqlite3.connect(tmp_db)
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            if tmp_db.exists():
                zf.write(tmp_db, "book.db")
            cfg = project_dir / "gennovel.yaml"
            if cfg.exists():
                zf.write(cfg, "gennovel.yaml")
            prompts = project_dir / "prompts"
            if prompts.is_dir():
                for f in sorted(prompts.glob("*.md")):
                    zf.write(f, f"prompts/{f.name}")
    finally:
        tmp_db.unlink(missing_ok=True)
    return out
