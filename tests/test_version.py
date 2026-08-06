"""Package version metadata must stay in sync."""

from __future__ import annotations

import tomllib
from pathlib import Path

import skycat


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_package_version_matches_runtime_version():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert skycat.__version__ == project["project"]["version"]
