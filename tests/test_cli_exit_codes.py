"""`skycat` must answer an operator, never a traceback — and never claim success
it did not earn.

Two contracts, both read by the K8s `skycat-ingest` Job:

*Exit codes.* `import <fam> <rel> --activate` once exited 0 even when activation
was *withheld* — so the Job went Complete while the release sat in READY and
default queries kept using old data. The truncation guard (a short source warns,
and warnings block auto-activation) made that path easy to hit.

*Messages, not tracebacks.* `_FriendlyGroup` originally caught four exception
types, which left the four most common operator mistakes — a stale
`SKYCAT_DB_PORT`, a rotated password, a mistyped `--ra`, an unknown family — each
printing thirty lines of Python at someone reading a Job log. Every case below
asserts the same three things: a message a human can act on, a non-zero exit, and
no `Traceback`.

No database anywhere in this module: the port is closed on purpose, the driver
error is constructed, and the coordinate/CSV/family failures all happen before a
connection is opened. `import_release` is substituted for the exit-code tests,
because what is under test is the CLI's decision about the report it gets back,
not the import itself.
"""

from __future__ import annotations

import os
import socket
from importlib import import_module

import psycopg
import pytest
from click.testing import CliRunner
from sqlalchemy.exc import OperationalError

from skycat.cli.main import main
from skycat.ingestion import IngestionError
from skycat.ingestion.runner import ImportReport

# `skycat.cli.__init__` re-exports `main`, so the name `skycat.cli.main` resolves
# to the Click group, not the module — `import skycat.cli.main as m` binds the
# group. Reach the module itself via sys.modules to patch its globals.
cli_main = import_module("skycat.cli.main")

BASE = dict(family="apass", release="DR6", target="t", source_dir="/s",
            parsed=6, loaded=6, imported=6, production_table="catalog_data.apass_source_r1")


@pytest.fixture
def run(monkeypatch):
    def _run(report_or_exc, *args):
        def fake_import(*_a, **_kw):
            if isinstance(report_or_exc, Exception):
                raise report_or_exc
            return report_or_exc

        monkeypatch.setattr(cli_main, "import_release", fake_import)
        return CliRunner().invoke(main, list(args), catch_exceptions=False)

    return _run


def test_withheld_activation_is_not_success(run):
    """--activate that did not activate must fail loudly, not exit 0."""
    report = ImportReport(
        **BASE, state="ready", validation_status="passed_with_warnings",
        activated=False,
        skipped_reason="not activated: validation warnings (use --allow-warnings)",
    )
    res = run(report, "import", "apass", "dr6", "--activate")
    assert res.exit_code != 0
    assert "NOT activated" in res.output
    assert "--allow-warnings" in res.output  # the operator's way out


def test_successful_activation_exits_zero(run):
    report = ImportReport(
        **BASE, state="active", validation_status="passed_with_warnings", activated=True
    )
    res = run(report, "import", "apass", "dr6", "--activate", "--allow-warnings")
    assert res.exit_code == 0
    assert "activated=True" in res.output


def test_skipped_active_activation_exits_zero(run):
    report = ImportReport(
        **BASE,
        state="active",
        validation_status="passed",
        activated=True,
        skipped_reason="already imported (matching checksum); use --replace to force",
    )
    res = run(report, "import", "apass", "dr6", "--activate")
    assert res.exit_code == 0
    assert "activated=True" in res.output
    assert "skipped: already imported" in res.output


def test_import_without_activate_exits_zero(run):
    """Nothing beyond the import was asked for, and the import worked."""
    report = ImportReport(
        **BASE, state="ready", validation_status="passed_with_warnings", activated=False
    )
    res = run(report, "import", "apass", "dr6")
    assert res.exit_code == 0


def test_failed_import_reports_a_message_not_a_traceback(run):
    """IngestionError is an operational outcome — a missing source, a bad file."""
    res = run(IngestionError("no source files under /catalog-data/APASS-DR10"),
              "import", "apass", "dr10")
    assert res.exit_code == 1
    assert "no source files" in res.output
    assert "Traceback" not in res.output


# ------------------------------------------------------- the error contract ---
def _closed_port() -> int:
    """A localhost TCP port with nothing listening on it.

    Bound-then-released rather than a hard-coded number: a fixed port is a coin
    flip on a developer's machine, and one that happened to be *open* would turn
    this database-free test into an integration test against something unknown.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    """A `SKYCAT_*` environment pointing at nothing.

    Every `SKYCAT_*` variable is cleared first, so the module behaves the same
    whether or not the caller exported credentials for the PostGIS suite.
    """
    for key in [k for k in os.environ if k.startswith("SKYCAT_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SKYCAT_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("SKYCAT_DB_PORT", str(_closed_port()))
    monkeypatch.setenv("SKYCAT_DB_NAME", "catalogs")
    monkeypatch.setenv("SKYCAT_DATA_ROOT", str(tmp_path))
    # catch_exceptions=False: an exception the CLI failed to translate propagates
    # out of invoke() and fails the test, which is the whole point here. Click's
    # standalone mode still turns a ClickException into `Error: …` + an exit code.
    return lambda *args: CliRunner().invoke(main, list(args), catch_exceptions=False)


def test_unreachable_database_is_a_message_not_a_traceback(cli_env):
    """A stale SKYCAT_DB_PORT — the runbook's most-regretted mistake."""
    res = cli_env("releases")
    assert res.exit_code == 2
    assert "Traceback" not in res.output
    assert "connection" in res.output.lower()


def test_bad_password_reports_the_driver_message_not_the_sql(cli_env, monkeypatch):
    """A rotated password. The driver's words are useful; the statement is not.

    `str(OperationalError)` appends `[SQL: …]` and `[parameters: …]`; echoing
    those into a terminal or a Job log is noise at best and a credential leak at
    worst, so the handler must surface `exc.orig` only.
    """
    def deny(*_a, **_kw):
        raise OperationalError(
            "SELECT catalog_registry.catalog_release.id FROM catalog_registry.catalog_release",
            {},
            psycopg.OperationalError(
                'connection failed: FATAL:  password authentication failed for '
                'user "catalog_reader"'
            ),
        )

    monkeypatch.setattr(cli_main, "list_releases", deny)
    res = cli_env("releases")
    assert res.exit_code == 2
    assert "Traceback" not in res.output
    assert "password authentication failed" in res.output
    assert "SELECT" not in res.output


def test_out_of_range_ra_is_a_message_not_a_traceback(cli_env):
    """A mistyped --ra never reaches the database."""
    res = cli_env("cone", "apass", "--ra", "400", "--dec", "0", "--radius-deg", "0.1")
    assert res.exit_code == 1
    assert "Traceback" not in res.output
    assert "RA out of range" in res.output


def test_unknown_family_to_discover_is_a_message_not_a_traceback(cli_env):
    res = cli_env("discover", "bogus", "x")
    assert res.exit_code == 1
    assert "Traceback" not in res.output
    assert "Unknown family" in res.output


def test_malformed_crossmatch_csv_is_a_message_not_a_traceback(cli_env, tmp_path):
    """A bad row must name itself: `id,ra,dec` is easy to get subtly wrong."""
    csv_path = tmp_path / "targets.csv"
    csv_path.write_text("id,ra,dec\nHD1,100.0039,4.861469\nHD2,not-a-number,4.9\n")
    res = cli_env("crossmatch", "apass", str(csv_path))
    assert res.exit_code == 1
    assert "Traceback" not in res.output
    assert "targets.csv" in res.output
    assert "not-a-number" in res.output


def test_debug_env_var_restores_the_traceback(cli_env, monkeypatch):  # noqa: ARG001
    """Friendly messages must not cost a developer the diagnosis.

    ``cli_env`` is requested for its side effect (a clean, unreachable
    environment); the invocation here needs the default ``catch_exceptions``, so
    the original exception can be inspected rather than propagated.
    """
    monkeypatch.setenv("SKYCAT_DEBUG", "1")
    res = CliRunner().invoke(main, ["discover", "bogus", "x"])
    assert isinstance(res.exception, ValueError)
