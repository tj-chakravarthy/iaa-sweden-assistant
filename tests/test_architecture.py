"""Layering checks.

core   imports no skill
skill  pure logic over core, except run.py which does the I/O
cli    names no skill, it serves registry.SKILLS
"""

import ast
from pathlib import Path

from iaa.core.policy import KNOWN_KINDS
from iaa.registry import SKILLS

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src" / "iaa"
IO_MODULES = ("connectors", "gspread", "googleapiclient", "requests", "typer", "google.oauth2", "auth", "config")


def imports(path: Path) -> list[str]:
    """Modules a file imports, as written, without the relative dots."""
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out += [f"{node.module or ''}.{a.name}".strip(".") for a in node.names]
    return out


def test_core_knows_no_skill():
    for path in (SRC / "core").rglob("*.py"):
        assert not [m for m in imports(path) if "skills" in m.split(".") or "registry" in m.split(".")], path


def test_cli_names_no_skill():
    source = (SRC / "cli.py").read_text(encoding="utf-8")
    assert not [m for m in imports(SRC / "cli.py") if "skills" in m.split(".")]
    assert all(f'"{s.name}"' not in source and f"'{s.name}'" not in source for s in SKILLS)


def test_a_skill_does_io_only_in_its_run_module():
    for skill in SKILLS:
        for path in (SRC / "skills" / skill.name).rglob("*.py"):
            if path.name == "run.py":
                continue
            bad = [m for m in imports(path) if any(part in IO_MODULES for part in m.split("."))]
            assert not bad, f"{path.relative_to(ROOT)} imports {bad}: I/O belongs in run.py"


def test_skills_do_not_reach_into_each_other():
    for skill in SKILLS:
        others = {s.name for s in SKILLS} - {skill.name}
        for path in (SRC / "skills" / skill.name).rglob("*.py"):
            assert not [m for m in imports(path) if set(m.split(".")) & others], path


def test_every_kind_has_one_skill_and_every_skill_its_page():
    claimed = [kind for skill in SKILLS for kind in skill.kinds]
    assert sorted(claimed) == sorted(KNOWN_KINDS)  # no kind without a skill, none claimed twice
    for skill in SKILLS:
        assert (ROOT / "docs" / "skills" / f"{skill.name}.md").is_file()
        assert (SRC / "skills" / skill.name / "run.py").is_file()


def test_the_members_tab_is_written_in_one_place():
    writers = [
        p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py") if 'write("members"' in p.read_text(encoding="utf-8")
    ]
    assert writers == ["skills/register/run.py"]
