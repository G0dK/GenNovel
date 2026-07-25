"""CLI：init / write / status / export / redo / serve。"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import DEFAULT_CONFIG_YAML
from .prompts import PromptLoader

app = typer.Typer(help="GenNovel — AI 长篇小说生产系统", no_args_is_help=True)
console = Console()


@app.callback(invoke_without_command=True)
def main(version: bool = typer.Option(False, "--version", "-V", help="显示版本号")):
    if version:
        console.print(f"gennovel {__version__}")
        raise typer.Exit()


def _engine(project: Path):
    from .engine import Engine
    return Engine(project, on_event=lambda msg: console.print(f"[dim]•[/dim] {msg}"))


@app.command()
def init(project: Path = typer.Argument(Path("."), help="项目目录")):
    """初始化书籍项目：生成配置与可编辑提示词。"""
    project.mkdir(parents=True, exist_ok=True)
    cfg = project / "gennovel.yaml"
    if cfg.exists():
        console.print(f"[yellow]{cfg} 已存在，跳过[/yellow]")
    else:
        cfg.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
        console.print(f"[green]已创建 {cfg}[/green]")
    PromptLoader(project).copy_defaults_to(project / "prompts")
    console.print(f"[green]已复制提示词模板到 {project / 'prompts'}（可自由修改）[/green]")
    console.print("\n下一步：")
    console.print("1. 编辑 gennovel.yaml，填写 book.premise 与 providers 的 API Key")
    console.print("2. 运行 [bold]gennovel write --chapters 1[/bold] 试写第一章")


@app.command()
def write(
    project: Path = typer.Option(Path("."), "--project", "-p", help="项目目录"),
    chapters: int = typer.Option(1, "--chapters", "-n", help="本次推进的章数"),
):
    """推进 N 章（自动补齐设定集/弧规划/节拍规划；断点自动续跑）。"""
    eng = _engine(project)
    try:
        done = eng.run(chapters)
        console.print(f"\n[bold green]本次完成 {done} 章[/bold green]")
        _print_status(eng.status())
    except KeyboardInterrupt:
        console.print("\n[yellow]已中断。进度保存到最后完成的阶段，再次运行 write 自动续跑。[/yellow]")
        raise typer.Exit(130) from None
    finally:
        eng.close()


@app.command()
def status(project: Path = typer.Option(Path("."), "--project", "-p")):
    """查看进度、警告与 token 用量。"""
    eng = _engine(project)
    try:
        _print_status(eng.status())
    finally:
        eng.close()


def _print_status(s: dict):
    t = Table(title=f"《{s['title']}》")
    t.add_column("指标")
    t.add_column("值")
    t.add_row("完成/规划/目标章数", f"{s['final']} / {s['planned']} / {s['target_chapters']}")
    t.add_row("已完成字数", f"{s['words']:,}")
    t.add_row("弧数", str(s["arcs"]))
    if s["current"]:
        t.add_row("当前进行", f"第 {s['current']} 章（阶段: {s['current_stage']}）")
    u = s["usage"]
    t.add_row("LLM 调用", f"{u['calls']} 次（失败 {u['errors']}），"
                        f"输入 {u['prompt_tokens']:,} / 输出 {u['completion_tokens']:,} tokens")
    if s.get("cost"):
        t.add_row("估算成本", f"{s['cost']:.2f}（按 providers 配置的单价）")
    console.print(t)
    for w in s["warnings"]:
        console.print(f"[yellow]⚠ 第{w['idx']}章: {w['warn']}[/yellow]")


@app.command()
def export(
    project: Path = typer.Option(Path("."), "--project", "-p"),
    fmt: str = typer.Option("md", "--format", "-f", help="md 或 txt"),
    out: Path = typer.Option(None, "--out", "-o"),
):
    """导出已完成章节为 Markdown/TXT。"""
    from .db import DB
    from .export import export_book
    db = DB(project / "book.db")
    try:
        out = out or project / f"book.{fmt}"
        path = export_book(db, out, fmt)
        console.print(f"[green]已导出 {path}[/green]")
    finally:
        db.close()


@app.command()
def redo(
    idx: int = typer.Argument(..., help="章号"),
    project: Path = typer.Option(Path("."), "--project", "-p"),
    from_stage: str = typer.Option("planned", "--from", help="planned/check/deslop/extract/summarize"),
):
    """把某章重置回指定阶段重跑。"""
    eng = _engine(project)
    try:
        eng.redo(idx, from_stage)
        console.print(f"[green]第 {idx} 章已重置到 {from_stage}，运行 write 继续[/green]")
    finally:
        eng.close()


@app.command()
def doctor(project: Path = typer.Option(Path("."), "--project", "-p")):
    """诊断：校验配置、检查密钥、逐个测试供应商连通性。"""
    from .config import load_config
    from .llm import LLMClient

    try:
        cfg = load_config(project)
    except Exception as e:
        console.print(f"[red]✗ 配置加载失败: {e}[/red]")
        raise typer.Exit(1) from None
    console.print("[green]✓ 配置解析正常[/green]")

    if not cfg.book.premise.strip() or cfg.book.premise.startswith("（必填）"):
        console.print("[yellow]⚠ book.premise 尚未填写，write 将无法开始[/yellow]")
    else:
        console.print("[green]✓ 故事前提已填写[/green]")

    if not cfg.providers:
        console.print("[red]✗ 未配置任何 provider[/red]")
        raise typer.Exit(1)

    client = LLMClient(cfg)
    failed = 0
    for p in cfg.providers:
        if p.api_key_env and not p.resolve_key():
            console.print(f"[yellow]⚠ {p.name}: 环境变量 {p.api_key_env} 为空[/yellow]")
        ok, ms, err = client.ping(p)
        if ok:
            console.print(f"[green]✓ {p.name} ({p.model}) 连通正常，{ms}ms[/green]")
        else:
            failed += 1
            console.print(f"[red]✗ {p.name} ({p.model}) 失败: {err}[/red]")
    if failed == len(cfg.providers):
        console.print("[red]所有供应商均不可用[/red]")
        raise typer.Exit(1)
    console.print("[bold green]诊断完成，可以开始写作[/bold green]")


@app.command()
def backup(
    project: Path = typer.Option(Path("."), "--project", "-p"),
    out: Path = typer.Option(None, "--out", "-o", help="输出 zip 路径"),
):
    """热备份：数据库（SQLite backup API，运行中也安全）+ 配置 + 提示词。"""
    from .export import backup_project
    path = backup_project(project, out)
    console.print(f"[green]已备份到 {path}[/green]")


@app.command()
def serve(
    project: Path = typer.Option(Path("."), "--project", "-p"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(13300, "--port"),
):
    """启动 Web 面板与 API（服务器部署入口）。"""
    import uvicorn

    from .server import create_app
    uvicorn.run(create_app(project), host=host, port=port)


if __name__ == "__main__":
    app()
