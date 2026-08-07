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

DOCS = [
    PKG_ROOT / "README.md",
    REPO_ROOT / "docs" / "reference" / "architecture.md",
    REPO_ROOT / "docs" / "reference" / "api-stability.md",
    REPO_ROOT / "docs" / "guides" / "add-family.md",
    REPO_ROOT / "docs" / "guides" / "provenance.md",
    REPO_ROOT / "docs" / "operations" / "runbook.md",
    REPO_ROOT / "docs" / "operations" / "performance.md",
    REPO_ROOT / "docs" / "operations" / "ci.md",
    REPO_ROOT / "docs" / "operations" / "release.md",
]
#: Placeholder paths/flags that are illustrative by design. ``tycho2`` is the
#: project's standing worked example for adding a family (README and
#: guides/add-family.md) — the day it becomes a real family, drop it from this
#: tuple and the citations start being checked.
PLACEHOLDERS = ("000N", "<family>", "<release>", "tycho2")


def _docs() -> list[Path]:
    # The package is a standalone uv project; the repo-level docs may not be
    # present in every checkout. Its own README always is.
    return [d for d in DOCS if d.exists()]


def _doc_id(path: Path) -> str:
    # Repo-relative, because ``docs/``, ``docs/decisions/``, and the repo root
    # each hold a README.md — bare names would collide into README.md0/1/2.
    return str(path.relative_to(REPO_ROOT))


def _all_markdown() -> list[Path]:
    """Every stable doc, for checks that need no code introspection.

    Broader than ``DOCS``: link and heading rot is worth catching in the design
    record and the decision log too, and neither costs anything to check.
    ``docs/working/`` stays excluded for the reason in the module docstring.
    """
    found = [PKG_ROOT / "README.md"]
    found += sorted(
        p
        for p in (REPO_ROOT / "docs").rglob("*.md")
        if "working" not in p.relative_to(REPO_ROOT).parts
    )
    return [p for p in found if p.exists()]


def _code_blocks(text: str, lang: str) -> list[str]:
    return [
        body
        for tag, body in re.findall(r"```(\w*)\n(.*?)```", text, re.S)
        if (tag == lang or (lang == "bash" and tag in ("sh", "console", "")))
    ]


#: ``skycat`` *invoked*, as opposed to ``skycat`` the package directory passed to
#: something else — ``uv run ruff check skycat tests`` is a correct command line
#: in which ``tests`` is not a subcommand. So the token has to sit in command
#: position, and every form this project's docs actually reach for is listed
#: below. Getting either direction wrong is silent: too loose and the test
#: invents commands nobody documented, too tight and it checks nothing at all.
_SKYCAT_INVOCATION = re.compile(
    r"""
    (?:
        ^                    # start of the (already stripped) line
      | [$|;]\s* | &&\s*     # after a prompt or a shell separator
      | (?:\w+=\S*\s+)+      # after inline env assignments: SKYCAT_DB_PORT=… skycat …
      | \brun\s+             # after a runner: uv run, poetry run, compose run
      | [\w.-]*/             # at the end of a path: /tmp/pkgcheck/bin/skycat
    )
    skycat\s+(.+)
    """,
    re.VERBOSE,
)


def _invocation(line: str) -> list[str] | None:
    """The argument tokens of a ``skycat`` command line, or None if it isn't one."""
    match = _SKYCAT_INVOCATION.search(line)
    return match.group(1).split() if match else None


#: What the matcher must and must not treat as a ``skycat`` invocation, pinned in
#: both directions. The regex is load-bearing — it decides whether a documented
#: page is checked or quietly skipped — and both failure modes have already
#: happened once: `ruff check skycat tests` was read as a `skycat tests` command,
#: and the fix for that initially stopped recognising an env-var prefix, which is
#: the most natural way to write an example in a project whose whole
#: configuration story is ``SKYCAT_DB_*``.
_INVOCATION_CASES = [
    # (documented line, expected first argument token or None for "not a command")
    ("skycat releases apass", "releases"),
    ("$ skycat cone apass --ra 10 --dec 1 --radius-arcmin 5", "cone"),
    ("uv run skycat init", "init"),
    ("uv run skycat --json discover", "--json"),
    ("/tmp/pkgcheck/bin/skycat --help", "--help"),
    ("SKYCAT_DB_PORT=5999 skycat releases", "releases"),
    ("SKYCAT_REQUIRE_POSTGIS=1 SKYCAT_DB_PORT=5442 skycat health", "health"),
    # `skycat` as an argument to something else, never as the command:
    ("uv run ruff check skycat tests", None),
    ("from skycat import CatalogReader", None),
    ("docker compose -f infra/docker/compose.yaml build skycat-postgres", None),
    (
        "docker compose -f infra/docker/compose.yaml --profile skycat-tools "
        "run --rm skycat-init",
        None,
    ),
]


@pytest.mark.parametrize("line,expected", _INVOCATION_CASES)
def test_invocation_matcher_requires_command_position(line, expected):
    tokens = _invocation(line)
    assert (tokens[0] if tokens else None) == expected


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


@pytest.mark.parametrize("doc", _docs(), ids=_doc_id)
def test_documented_cli_commands_and_flags_exist(doc):
    problems = []
    for block in _code_blocks(doc.read_text(encoding="utf-8"), "bash"):
        for raw in block.splitlines():
            line = raw.split("#", 1)[0].strip()
            tokens = _invocation(line)
            if tokens is None:
                continue
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


@pytest.mark.parametrize("doc", _docs(), ids=_doc_id)
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


@pytest.mark.parametrize("doc", _docs(), ids=_doc_id)
def test_cited_source_files_exist(doc):
    missing = [
        path
        for path in set(re.findall(r"`(skycat/[\w/]+\.py)`", doc.read_text(encoding="utf-8")))
        if not any(p in path for p in PLACEHOLDERS) and not (PKG_ROOT / path).exists()
    ]
    assert not missing, f"{doc.name} cites files that do not exist: {missing}"


@pytest.mark.parametrize("doc", _docs(), ids=_doc_id)
def test_group_flags_precede_the_subcommand(doc):
    """``skycat cone apass --json`` is a documented command that cannot run.

    ``--json`` is declared on the Click *group*, so it is only accepted before
    the subcommand name. Click rejects it afterwards with "No such option",
    which the command/flag test above cannot see: it accepts a group flag
    anywhere on the line, since that is the only way to tell a group flag from
    an unknown one.
    """
    problems = []
    for block in _code_blocks(doc.read_text(encoding="utf-8"), "bash"):
        for raw in block.splitlines():
            line = raw.split("#", 1)[0].strip()
            tokens = _invocation(line)
            if tokens is None:
                continue
            sub_index = next(
                (i for i, t in enumerate(tokens) if not t.startswith("-")), None
            )
            if sub_index is None:
                continue
            for token in tokens[sub_index + 1 :]:
                flag = token.split("=", 1)[0]
                if flag in GROUP_FLAGS and flag not in COMMANDS.get(
                    tokens[sub_index], ()
                ):
                    problems.append(
                        f"{flag} is a group option and must come before "
                        f"`{tokens[sub_index]}`  ({line})"
                    )
    assert not problems, f"{doc.name} documents a CLI invocation that fails:\n  " + "\n  ".join(
        problems
    )


#: Link targets that are not repository paths.
_EXTERNAL_LINK = re.compile(r"^(https?:|mailto:|#)")


@pytest.mark.parametrize("doc", _all_markdown(), ids=_doc_id)
def test_relative_links_resolve(doc):
    """A moved doc takes its links with it, and nothing notices.

    The design record (now ``docs/reference/architecture.md``) was moved twice
    and pointed at ``../../../skycat/...`` — one level above the repository
    root — the whole time. Neither ruff, pyright, nor the citation test above
    could see it, because a markdown link is not a backticked path. This test
    is what makes the ``docs/`` tree safe to reorganize.
    """
    text = doc.read_text(encoding="utf-8")
    broken = []
    for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", text):
        if _EXTERNAL_LINK.match(target):
            continue
        path, _, _anchor = target.partition("#")
        if not path or any(p in path for p in PLACEHOLDERS):
            continue
        if not (doc.parent / path).resolve().exists():
            broken.append(target)
    rel = doc.relative_to(REPO_ROOT)
    assert not broken, f"{rel} links to paths that do not exist: {sorted(set(broken))}"
