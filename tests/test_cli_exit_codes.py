"""`skycat import` must not report success when it did not do what was asked.

The K8s `skycat-ingest` Job's only success signal is the exit code. Before this,
`import <fam> <rel> --activate` exited 0 even when activation was *withheld* —
so the Job went Complete while the release sat in READY and default queries kept
using old data. The truncation guard (a short source warns, and warnings block
auto-activation) made that path easy to hit.

No database: `import_release` is substituted, because what is under test is the
CLI's decision about the report it gets back, not the import itself.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from click.testing import CliRunner

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
