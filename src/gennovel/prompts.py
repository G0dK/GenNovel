"""提示词模板加载：项目 prompts/ 目录优先，缺省回落到包内 defaults/。

模板格式：`<!-- system -->` 与 `<!-- user -->` 分节，变量用 string.Template 的 $name
（相比 str.format，$ 语法不会与模板里 JSON 示例的花括号冲突）。
"""
from __future__ import annotations

from pathlib import Path
from string import Template

DEFAULTS_DIR = Path(__file__).parent / "defaults"

TEMPLATE_NAMES = [
    "bible", "arcs", "beats", "draft", "check", "revise",
    "deslop", "extract", "summarize", "long_summary", "prose_rules",
]


class PromptLoader:
    def __init__(self, project_dir: str | Path):
        self.project_prompts = Path(project_dir) / "prompts"

    def _read(self, name: str) -> str:
        custom = self.project_prompts / f"{name}.md"
        if custom.exists():
            return custom.read_text(encoding="utf-8")
        default = DEFAULTS_DIR / f"{name}.md"
        if not default.exists():
            raise FileNotFoundError(f"提示词模板不存在: {name}")
        return default.read_text(encoding="utf-8")

    def prose_rules(self) -> str:
        return self._read("prose_rules")

    def render(self, name: str, **vars) -> tuple[str, str]:
        """返回 (system, user)。未提供的变量原样保留（safe_substitute），便于排查。"""
        raw = self._read(name)
        system, user = "", raw
        if "<!-- user -->" in raw:
            head, user = raw.split("<!-- user -->", 1)
            system = head.replace("<!-- system -->", "").strip()
        str_vars = {k: (v if isinstance(v, str) else str(v)) for k, v in vars.items()}
        return (
            Template(system).safe_substitute(str_vars).strip(),
            Template(user).safe_substitute(str_vars).strip(),
        )

    def copy_defaults_to(self, dest: str | Path):
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        for name in TEMPLATE_NAMES:
            target = dest / f"{name}.md"
            if not target.exists():
                target.write_text((DEFAULTS_DIR / f"{name}.md").read_text(encoding="utf-8"),
                                  encoding="utf-8")
