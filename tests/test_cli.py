"""CLI surface (marker: postgis).

The query commands are the operator-facing half of the package and had no
coverage: the `cone` flags and the friendly-error wrapper were only ever
exercised by hand.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from skycat.cli.main import main

pytestmark = pytest.mark.postgis

CENTER = ["--ra", "100.0039", "--dec", "4.861469", "--radius-deg", "0.5"]


@pytest.fixture
def run(imported, monkeypatch):
    """Invoke the CLI against the test database (settings come from the env)."""
    runner = CliRunner()

    def _run(*args):
        return runner.invoke(main, list(args), catch_exceptions=False)

    return _run


def test_cone_defaults_to_nearest_first(run):
    res = run("--json", "cone", "apass", *CENTER, "--limit", "3")
    assert res.exit_code == 0
    rows = json.loads(res.output)
    seps = [r["separation_deg"] for r in rows]
    assert seps == sorted(seps)


def test_cone_order_by_returns_brightest_first(run):
    res = run(
        "--json", "cone", "apass", *CENTER, "--limit", "3",
        "--order-by", "johnson_v_mag",
    )
    assert res.exit_code == 0
    rows = json.loads(res.output)
    mags = [r["johnson_v_mag"] for r in rows]
    assert mags == sorted(mags)
    # The brightest star in this field is the farthest from the centre — the
    # star a nearest-N cap silently drops.
    assert rows[0]["native_id"] == "090-0000004"


def test_cone_order_by_changes_the_selected_stars(run):
    near = json.loads(run("--json", "cone", "apass", *CENTER, "--limit", "3").output)
    bright = json.loads(
        run(
            "--json", "cone", "apass", *CENTER, "--limit", "3",
            "--order-by", "johnson_v_mag",
        ).output
    )
    assert {r["native_id"] for r in near} != {r["native_id"] for r in bright}


def test_explain_reflects_the_ordering(run):
    plan = run("cone", "apass", *CENTER, "--explain").output
    bright = run(
        "cone", "apass", *CENTER, "--explain", "--order-by", "johnson_v_mag"
    ).output
    assert "johnson_v_mag" not in plan
    assert "johnson_v_mag" in bright


class TestFriendlyErrors:
    """Operator input errors report a message, not a traceback."""

    def test_bad_order_by_column(self, run):
        res = run("cone", "apass", *CENTER, "--order-by", "native_id")
        assert res.exit_code == 1
        assert "not numeric" in res.output

    def test_unknown_order_by_column(self, run):
        res = run("cone", "apass", *CENTER, "--order-by", "nope")
        assert res.exit_code == 1
        assert "Unknown order-by column" in res.output

    def test_unknown_family(self, run):
        res = run("cone", "nosuch", *CENTER)
        assert res.exit_code == 1
        assert "Unknown family" in res.output
