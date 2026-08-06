"""The documented API must exist. No database needed.

Linters find code nothing references; nothing finds *documentation* that
references code which no longer exists. That gap is not hypothetical here: the
README's add-a-family checklist told people to build models with
``native_id_column()`` / ``ra_deg_column()`` / ``dec_deg_column()`` helpers that
no model had ever used — the code and the docs had rotted together, and neither
ruff, pyright, nor vulture could see it.

So: every ``skycat`` command and flag shown in the docs must be real, and every
``CatalogReader`` method and keyword argument in a Python example must exist.

Only stable user-facing docs are checked. Working review notes under
``docs/working/`` are deliberately excluded: they are dated snapshots of open
work, not descriptions of the current API, and pinning them to today's code
would be wrong.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from skycat import CatalogReader
from skycat.cli.main import main as cli_group

PKG_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PKG_ROOT

DOCS = [PKG_ROOT / "README.md", REPO_ROOT / "docs" / "SKYCAT_DATABASE.md"]
#: Placeholder paths/flags that are illustrative by design.
PLACEHOLDERS = ("000N", "<family>", "<release>")


def _docs() -> list[Path]:
    # The package is a standalone uv project; the repo-level docs may not be
    # present in every checkout. Its own README always is.
    return [d for d in DOCS if d.exists()]


def _code_blocks(text: str, lang: str) -> list[str]:
    return [
        body
        for tag, body in re.findall(r"```(\w*)\n(.*?)```", text, re.S)
        if (tag == lang or (lang == "bash" and tag in ("sh", "console", "")))
    ]


def _cli_surface() -> tuple[dict[str, set[str]], set[str]]:
    commands = {}
    for name, cmd in cli_group.commands.items():
        flags = set()
        for param in cmd.params:
            flags.update(o for o in param.opts if o.startswith("--"))
            flags.update(o for o in param.secondary_opts if o.startswith("--"))
        commands[name] = flags
    group_flags = {o for p in cli_group.params for o in p.opts if o.startswith("--")}
    return commands, group_flags


COMMANDS, GROUP_FLAGS = _cli_surface()


@pytest.mark.parametrize("doc", _docs(), ids=lambda p: p.name)
def test_documented_cli_commands_and_flags_exist(doc):
    problems = []
    for block in _code_blocks(doc.read_text(encoding="utf-8"), "bash"):
        for raw in block.splitlines():
            line = raw.split("#", 1)[0].strip()
            match = re.search(r"\bskycat\b\s+(.*)", line)
            if not match:
                continue
            tokens = match.group(1).split()
            sub = next((t for t in tokens if not t.startswith("-")), None)
            if sub is None or any(p in sub for p in PLACEHOLDERS):
                continue
            if sub not in COMMANDS:
                problems.append(f"`skycat {sub}` is not a command  ({line})")
                continue
            for token in tokens:
                if not token.startswith("--"):
                    continue
                flag = token.split("=", 1)[0]
                if flag not in COMMANDS[sub] and flag not in GROUP_FLAGS:
                    problems.append(f"`skycat {sub}` has no flag {flag}  ({line})")
    assert not problems, f"{doc.name} documents a CLI that does not exist:\n  " + "\n  ".join(
        problems
    )


@pytest.mark.parametrize("doc", _docs(), ids=lambda p: p.name)
def test_documented_catalogreader_api_exists(doc):
    """Methods and kwargs shown on a CatalogReader must be real."""
    problems = []
    for block in _code_blocks(doc.read_text(encoding="utf-8"), "python"):
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue  # an illustrative fragment, not runnable code

        readers = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
            for target in node.targets
            if isinstance(target, ast.Name) and _constructs_reader(node.value.func)
        }
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in readers
            ):
                continue
            method = getattr(CatalogReader, node.func.attr, None)
            if method is None:
                problems.append(f"CatalogReader.{node.func.attr}() — no such method")
                continue
            params = inspect.signature(method).parameters
            for kw in node.keywords:
                if kw.arg and kw.arg not in params:
                    problems.append(
                        f"CatalogReader.{node.func.attr}({kw.arg}=...) — not a parameter"
                    )
    assert not problems, f"{doc.name} documents an API that does not exist:\n  " + "\n  ".join(
        problems
    )


def _constructs_reader(func: ast.expr) -> bool:
    if isinstance(func, ast.Name):
        return func.id == "CatalogReader"
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id == "CatalogReader"  # e.g. CatalogReader.from_env()
    return False


@pytest.mark.parametrize("doc", _docs(), ids=lambda p: p.name)
def test_cited_source_files_exist(doc):
    missing = [
        path
        for path in set(re.findall(r"`(skycat/[\w/]+\.py)`", doc.read_text(encoding="utf-8")))
        if not any(p in path for p in PLACEHOLDERS) and not (PKG_ROOT / path).exists()
    ]
    assert not missing, f"{doc.name} cites files that do not exist: {missing}"
