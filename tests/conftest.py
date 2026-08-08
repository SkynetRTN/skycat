"""Shared test fixtures.

Unit tests run anywhere. PostGIS integration tests (marker ``postgis``) require a
reachable catalog database, configured via the standard ``SKYCAT_DB_*``
environment (e.g. the Compose `skycat-postgres` on 127.0.0.1:5433). They are
skipped automatically when no catalog DB is reachable.

That default is right for casual runs and wrong for release validation: a green
suite proves nothing about migrations, roles, COPY, partitions, or spatial
indexes if every test that touches them was quietly skipped. Pass
``--require-postgis`` (or set ``SKYCAT_REQUIRE_POSTGIS=1``) to make an
unreachable database a hard error instead — see README "Testing".
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from skycat.config import CatalogRole, CatalogSettings
from skycat.database.engine import create_catalog_engine

DATA_DIR = Path(__file__).parent / "data"

REQUIRE_POSTGIS_ENV = "SKYCAT_REQUIRE_POSTGIS"

_UNREACHABLE = (
    "no reachable catalog PostGIS database — point SKYCAT_DB_* at a THROWAWAY "
    "PostGIS (see README > Testing). Never a provisioned catalog store: the "
    "`imported` fixture replaces every release with six-row samples."
)


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--require-postgis",
        action="store_true",
        default=False,
        help=(
            "Fail instead of skipping when no catalog PostGIS database is "
            "reachable. Use for release validation, where a silently "
            "degraded unit-only run is indistinguishable from a passing one."
        ),
    )


def _required(config) -> bool:
    return bool(config.getoption("--require-postgis")) or os.environ.get(
        REQUIRE_POSTGIS_ENV, ""
    ).lower() in ("1", "true", "yes")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items) -> None:
    """Fail the whole run up front rather than per test.

    The failure mode this guards against is environmental, not per-test: one
    clear message beats the same connection error repeated across every
    integration test.

    ``trylast`` so ``-m``/``-k`` deselection has already run: asking for
    ``-m "not postgis" --require-postgis`` is not a contradiction, it is a unit
    run that happens to have the flag set in the environment.
    """
    if not _required(config):
        return
    if not any(item.get_closest_marker("postgis") for item in items):
        return
    if _reachable(CatalogSettings.from_env()):
        return
    raise pytest.UsageError(
        f"--require-postgis was given but {_UNREACHABLE}\n"
        "Run `skycat health` against the same SKYCAT_DB_* environment to see "
        "which part of the connection contract is missing."
    )


def _reachable(settings: CatalogSettings) -> bool:
    try:
        engine = create_catalog_engine(
            settings.config_for(CatalogRole.READER), pool_size=1, max_overflow=0
        )
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def settings() -> CatalogSettings:
    return CatalogSettings.from_env()


@pytest.fixture(scope="session")
def fixture_data_root(tmp_path_factory) -> Path:
    """A temp data root mirroring the real catalog layout, populated from the
    committed sample fixtures."""
    root = tmp_path_factory.mktemp("catalog-data")
    (root / "APASS-DR6").mkdir()
    (root / "APASS-DR10").mkdir()
    (root / "VSX").mkdir()
    (root / "Landolt_1992").mkdir()
    (root / "Landolt_2009").mkdir()
    (root / "StetsonGlobs").mkdir()
    shutil.copy(DATA_DIR / "apass_dr6_sample.sum", root / "APASS-DR6" / "zp00_6.sum")
    shutil.copy(DATA_DIR / "apass_dr10_sample.txt", root / "APASS-DR10" / "zp00.txt")
    shutil.copy(DATA_DIR / "vsx_sample.dat", root / "VSX" / "vsx.dat")
    shutil.copy(DATA_DIR / "landolt_1992_sample.dat", root / "Landolt_1992" / "table2.dat")
    shutil.copy(DATA_DIR / "landolt_2009_sample.dat", root / "Landolt_2009" / "table2.dat")
    shutil.copy(DATA_DIR / "stetson_globs_sample.dat", root / "StetsonGlobs" / "table4.dat")
    return root


@pytest.fixture(scope="session")
def pg(request, settings) -> CatalogSettings:
    if not _reachable(settings):
        # --require-postgis already aborted collection; this is the ordinary
        # path, where skipping is the documented behaviour.
        if _required(request.config):
            pytest.fail(_UNREACHABLE, pytrace=False)
        pytest.skip(_UNREACHABLE)
    return settings


@pytest.fixture(scope="session")
def imported(pg, fixture_data_root) -> CatalogSettings:
    """Initialize the DB and import all three sample releases (idempotent)."""
    import dataclasses

    from skycat.database.init import initialize_catalog_database
    from skycat.ingestion import import_release

    s = dataclasses.replace(pg, data_root=str(fixture_data_root))
    initialize_catalog_database(s)
    # Landolt is imported 1992 then 2009 with activate=True, so 2009 ends ACTIVE
    # and 1992 SUPERSEDED (queryable explicitly) — mirroring the production layout.
    for fam, rel in [
        ("apass", "dr6"), ("apass", "dr10"), ("vsx", "current"),
        ("landolt", "1992"), ("landolt", "2009"), ("stetson", "stetsonglobs"),
    ]:
        import_release(
            s, fam, rel, activate=True, allow_warnings=True, replace=True, force=True
        )
    return s
