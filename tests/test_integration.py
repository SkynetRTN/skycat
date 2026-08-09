"""PostGIS integration tests (marker: postgis).

Exercise the real database path: schemas, registry, release lifecycle,
spatial cone search (wraparound + poles + separation), index usage, native
lookup, batch crossmatch, role permissions, and data-safety guards. Requires a
reachable catalog PostGIS database (skipped otherwise — see conftest).
"""

from __future__ import annotations

from importlib import import_module

import pytest
from click.testing import CliRunner
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from skycat.config import CatalogConfigError, CatalogRole
from skycat.constants import ALL_SCHEMAS
from skycat.database.engine import create_catalog_engine
from skycat.ingestion import import_release
from skycat.ingestion.maintenance import remove_release, revalidate_release
from skycat.validation.common import validate_production
from skycat.query import (
    CatalogQueryError,
    batch_crossmatch,
    cone_search,
    cone_search_plan,
    lookup_native_id,
)
from skycat.spatial import angular_separation_deg
from skycat.registry import activate_release, resolve_release

pytestmark = pytest.mark.postgis


def _reader(settings):
    return create_catalog_engine(
        settings.config_for(CatalogRole.READER), pool_size=1, max_overflow=0
    )


def _ingest(settings):
    return create_catalog_engine(
        settings.config_for(CatalogRole.INGEST), pool_size=1, max_overflow=0
    )


def _activate(settings, family: str, name: str) -> None:
    eng = _ingest(settings)
    try:
        with Session(eng) as session:
            rel = resolve_release(session, family, name)
            assert rel is not None
            activate_release(session, rel)
            session.commit()
    finally:
        eng.dispose()


def _release_state(settings, family: str, name: str) -> str:
    eng = _reader(settings)
    try:
        with eng.connect() as conn:
            return conn.execute(
                text(
                    "SELECT r.state FROM catalog_registry.catalog_release r "
                    "JOIN catalog_registry.catalog_family f ON f.id = r.family_id "
                    "WHERE f.slug = :family AND r.name = :name"
                ),
                {"family": family, "name": name},
            ).scalar_one()
    finally:
        eng.dispose()


def _invoke_cli(settings, monkeypatch, *args):
    cli_main = import_module("skycat.cli.main")
    monkeypatch.setattr(cli_main, "load_settings", lambda: settings)
    return CliRunner().invoke(cli_main.main, list(args), catch_exceptions=False)


def test_schemas_and_postgis(imported):
    eng = _reader(imported)
    with eng.connect() as conn:
        present = set(conn.execute(text("SELECT nspname FROM pg_namespace")).scalars())
        for schema in ALL_SCHEMAS:
            assert schema in present
        assert conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname='postgis'")
        ).scalar()
    eng.dispose()


def test_partitions_and_release_states(imported):
    eng = _reader(imported)
    with eng.connect() as conn:
        parts = set(
            conn.execute(
                text(
                    "SELECT inhrelid::regclass::text FROM pg_inherits "
                    "WHERE inhparent='catalog_data.apass_source'::regclass"
                )
            ).scalars()
        )
        assert any(p.endswith("apass_source_r1") for p in parts)
        assert any(p.endswith("apass_source_r2") for p in parts)
        states = dict(
            conn.execute(
                text(
                    "SELECT r.name, r.state FROM catalog_registry.catalog_release r "
                    "JOIN catalog_registry.catalog_family f ON f.id=r.family_id WHERE f.slug='apass'"
                )
            ).all()
        )
    eng.dispose()
    # Exactly one active release per family; DR10 newest -> active, DR6 superseded.
    assert states["DR10"] == "active"
    assert states["DR6"] == "superseded"


def test_cone_active_and_explicit_release(imported):
    # Active apass == DR10: search near a DR10 point.
    rows = cone_search(imported, "apass", 100.0039, 4.861469, radius_deg=0.5)
    assert rows and rows[0]["native_id"] == "090-0000001"
    assert rows == sorted(rows, key=lambda r: r["separation_deg"])
    # DR6 remains queryable explicitly after DR10 activation.
    dr6 = cone_search(
        imported, "apass", 0.000236, 1.886943, radius_deg=0.1, release="DR6"
    )
    assert any(r["native_id"] == "0020120136" for r in dr6)


def test_import_activate_on_already_active_skip_exits_zero(imported, monkeypatch):
    _activate(imported, "apass", "DR10")

    res = _invoke_cli(imported, monkeypatch, "import", "apass", "dr10", "--activate")

    assert res.exit_code == 0, res.output
    assert "activated=True" in res.output
    assert "skipped: already imported" in res.output
    assert _release_state(imported, "apass", "DR10") == "active"


def test_import_activate_on_skipped_ready_release_activates(imported, monkeypatch):
    _activate(imported, "apass", "DR10")
    import_release(imported, "apass", "dr6", replace=True, force=True)
    assert _release_state(imported, "apass", "DR6") == "ready"

    try:
        res = _invoke_cli(
            imported,
            monkeypatch,
            "import",
            "apass",
            "dr6",
            "--activate",
            "--allow-warnings",
        )
        assert res.exit_code == 0, res.output
        assert "activated=True" in res.output
        assert "skipped: already imported" in res.output
        assert _release_state(imported, "apass", "DR6") == "active"
    finally:
        _activate(imported, "apass", "DR10")


def test_cone_ra_wraparound(imported):
    # DR6 point sits at RA≈0.0002; a cone centred at RA 359.95 must still find it.
    rows = cone_search(
        imported, "apass", 359.95, 1.886943, radius_deg=0.2, release="DR6"
    )
    assert any(r["native_id"] == "0020120136" for r in rows)


def test_cone_high_declination(imported):
    # VSX fixture has a source at dec ≈ -75.87 (high southern declination).
    rows = cone_search(imported, "vsx", 0.00006, -75.86906, radius_deg=1.0)
    assert any(r["vsx_oid"] == 8278100 for r in rows)


def test_separation_matches_python(imported):
    rows = cone_search(imported, "apass", 100.0039, 4.861469, radius_deg=1.0)
    assert len(rows) >= 2
    for r in rows:
        expected = angular_separation_deg(100.0039, 4.861469, r["ra_deg"], r["dec_deg"])
        assert r["separation_deg"] == pytest.approx(expected, abs=1e-6)


def test_cone_uses_spatial_index(imported):
    """With seqscan disabled the planner must use the GiST index for the cone."""
    from skycat.spatial import degrees_to_meters

    eng = _reader(imported)
    radius_m = degrees_to_meters(0.5)
    with eng.connect() as conn:
        rid = conn.execute(
            text(
                "SELECT r.id FROM catalog_registry.catalog_release r "
                "JOIN catalog_registry.catalog_family f ON f.id=r.family_id "
                "WHERE f.slug='apass' AND r.state='active'"
            )
        ).scalar()
        conn.exec_driver_sql("SET LOCAL enable_seqscan = off")
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                text(
                    f"EXPLAIN SELECT id FROM catalog_data.apass_source "
                    f"WHERE release_id = {rid} AND ST_DWithin(geom, "
                    f"ST_SetSRID(ST_MakePoint(100.0039, 4.861469),4326)::geography, {radius_m}, false)"
                )
            ).all()
        )
    eng.dispose()
    assert "Index Scan" in plan or "Bitmap Index Scan" in plan or "gist" in plan.lower()


def test_brightest_first_still_uses_the_spatial_index(imported):
    """order_by must not cost the cone its index.

    The magnitude sort applies to the cone's candidate rows, not the table: the
    GiST index still does the filtering, so brightest-first stays viable on a
    128M-row release.
    """
    from skycat.spatial import degrees_to_meters

    eng = _reader(imported)
    radius_m = degrees_to_meters(0.5)
    with eng.connect() as conn:
        rid = conn.execute(
            text(
                "SELECT r.id FROM catalog_registry.catalog_release r "
                "JOIN catalog_registry.catalog_family f ON f.id=r.family_id "
                "WHERE f.slug='apass' AND r.state='active'"
            )
        ).scalar()
        conn.exec_driver_sql("SET LOCAL enable_seqscan = off")
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                text(
                    f"EXPLAIN SELECT id FROM catalog_data.apass_source "
                    f"WHERE release_id = {rid} AND ST_DWithin(geom, "
                    f"ST_SetSRID(ST_MakePoint(100.0039, 4.861469),4326)::geography, "
                    f"{radius_m}, false) "
                    f"ORDER BY johnson_v_mag ASC NULLS LAST LIMIT 500"
                )
            ).all()
        )
    eng.dispose()
    assert "Index Scan" in plan or "Bitmap Index Scan" in plan or "gist" in plan.lower()


def test_cone_order_by_selects_brightest_not_nearest(imported):
    """Under a limit, brightest-first and nearest-first return different stars.

    In the DR10 fixture the brightest source in the cone (090-0000004, V=14.251)
    is also the *farthest* from the centre, so a nearest-N cap drops it — the
    calibration-parity failure order_by exists to prevent.
    """
    center = (100.0039, 4.861469)
    kwargs = {"radius_deg": 0.5, "limit": 3}
    nearest = cone_search(imported, "apass", *center, **kwargs)
    brightest = cone_search(
        imported, "apass", *center, order_by="johnson_v_mag", **kwargs
    )

    seps = [r["separation_deg"] for r in nearest]
    assert seps == sorted(seps)

    mags = [r["johnson_v_mag"] for r in brightest]
    assert mags == sorted(mags)
    assert brightest[0]["native_id"] == "090-0000004"
    assert {r["native_id"] for r in brightest} != {r["native_id"] for r in nearest}
    # ...and the brightest star the nearest-N cap would have missed is in there.
    assert brightest[0]["separation_deg"] > max(seps)


def test_cone_order_by_rejects_unknown_column(imported):
    with pytest.raises(CatalogQueryError, match="Unknown order-by column"):
        cone_search(
            imported, "apass", 100.0039, 4.861469, radius_deg=0.5, order_by="nope"
        )


def test_explain_plan_reflects_the_requested_ordering(imported):
    """--explain must explain the query that runs, not a nearest-first stand-in.

    Ordering by magnitude adds a sort over the cone's candidate rows; a plan that
    omitted it would misrepresent the cost of the query the caller asked for.
    """
    nearest = cone_search_plan(imported, "apass", 100.0039, 4.861469, radius_deg=0.5)
    brightest = cone_search_plan(
        imported, "apass", 100.0039, 4.861469, radius_deg=0.5,
        order_by="johnson_v_mag",
    )
    assert "johnson_v_mag" not in nearest
    assert "johnson_v_mag" in brightest
    # Sorting by magnitude is what adds the sort step over the cone's rows.
    assert "Sort Key" in brightest


def test_explain_plan_validates_the_order_by_column(imported):
    with pytest.raises(CatalogQueryError, match="Unknown order-by column"):
        cone_search_plan(
            imported, "apass", 100.0039, 4.861469, radius_deg=0.5,
            order_by="1=1; DROP TABLE catalog_data.apass_source; --",
        )


def test_short_import_warns_against_the_expected_row_count(imported):
    """The 6-row APASS sample stands in for a truncated source: it must warn.

    A short read of a multi-GB catalog parses cleanly and would otherwise import,
    validate and activate as a silently incomplete release.
    """
    res = revalidate_release(imported, "apass", "dr10")
    assert res["status"] == "passed_with_warnings"
    check = next(c for c in res["checks"] if c["name"] == "row_count_vs_expected")
    assert check["level"] == "warning" and not check["passed"]
    assert "truncated" in check["detail"]


def test_row_count_check_is_skipped_without_an_expectation(imported):
    """A family with no published size must not have a count invented for it."""
    eng = _reader(imported)
    with eng.connect() as conn:
        checks = validate_production(
            conn, "catalog_data", "apass_source_r2", expected_row_count=None
        )
    eng.dispose()
    names = {c.name for c in checks}
    assert "row_count_vs_expected" not in names
    assert "production_rows" in names  # the other checks still run


def test_expected_row_count_is_recorded_from_the_family_def(imported):
    eng = _reader(imported)
    with eng.connect() as conn:
        expected = conn.execute(
            text(
                "SELECT r.expected_row_count FROM catalog_registry.catalog_release r "
                "JOIN catalog_registry.catalog_family f ON f.id=r.family_id "
                "WHERE f.slug='apass' AND r.name='DR10'"
            )
        ).scalar()
    eng.dispose()
    assert expected == 128_632_615


def test_default_release_id_column_is_gone(imported):
    """Dropped in 0006: the active release is resolved from state, one source."""
    eng = _reader(imported)
    with eng.connect() as conn:
        cols = set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='catalog_registry' AND table_name='catalog_family'"
                )
            ).scalars()
        )
    eng.dispose()
    assert "default_release_id" not in cols
    assert {"slug", "data_table", "enabled"} <= cols  # the table itself is intact


def test_native_id_lookup(imported):
    rows = lookup_native_id(imported, "apass", "090-0000001")
    assert len(rows) == 1 and rows[0]["ra_deg"] == pytest.approx(100.0039)


def test_batch_crossmatch(imported):
    inputs = [("a", 100.0039, 4.861469), ("b", 100.010294, 4.962730), ("c", 12.0, 80.0)]
    rows = batch_crossmatch(imported, "apass", inputs, radius_deg=30 / 3600.0)
    by_id = {r["input_id"]: r for r in rows}
    assert by_id["a"]["matched"] and by_id["a"]["native_id"] == "090-0000001"
    assert by_id["b"]["matched"]
    assert not by_id["c"]["matched"]


def test_batch_crossmatch_skips_invalid_inputs(imported):
    # One valid input + one out-of-range (|dec| > 90). The bad input must NOT
    # abort the batch — pre-fix it failed the generated-geography cast at COPY
    # time and lost every result. It now comes back matched=False, and the good
    # input still matches.
    inputs = [("good", 100.0039, 4.861469), ("bad-dec", 100.0, 123.0)]
    rows = batch_crossmatch(imported, "apass", inputs, radius_deg=30 / 3600.0)
    by_id = {r["input_id"]: r for r in rows}
    assert by_id["good"]["matched"] and by_id["good"]["native_id"] == "090-0000001"
    assert by_id["bad-dec"]["matched"] is False
    assert by_id["bad-dec"]["separation_deg"] is None


def test_reader_is_read_only(imported):
    eng = _reader(imported)
    with eng.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM catalog_data.apass_source")
            ).scalar()
            > 0
        )
    with eng.connect() as conn:
        with pytest.raises(ProgrammingError):
            conn.execute(
                text(
                    "INSERT INTO catalog_data.apass_source(release_id,native_id,ra_deg,dec_deg) "
                    "VALUES (1,'x',1,1)"
                )
            )
    eng.dispose()


def test_prevent_multiple_active(imported):
    """The partial unique index forbids two active releases per family."""
    eng = create_catalog_engine(
        imported.config_for(CatalogRole.INGEST), pool_size=1, max_overflow=0
    )
    with eng.connect() as conn:
        trans = conn.begin()
        with pytest.raises(IntegrityError):
            # DR10 is active; forcing DR6 active too must violate the index.
            conn.execute(
                text(
                    "UPDATE catalog_registry.catalog_release SET state='active' "
                    "WHERE name='DR6' AND family_id=("
                    "  SELECT id FROM catalog_registry.catalog_family WHERE slug='apass')"
                )
            )
        trans.rollback()
    eng.dispose()


def test_active_release_deletion_protected(imported):
    with pytest.raises(CatalogConfigError):
        remove_release(imported, "apass", "DR10")  # active, no --force


def _register_removable_release(settings, name: str, table_template: str) -> tuple[int, str]:
    """Register an inactive APASS release with a real attached partition and an
    ingestion run, so the destructive path has something to destroy.

    Synthetic rather than one of the fixture's six releases: the fixture is
    session-scoped, other tests in this module pin ``apass_source_r1`` and
    ``_r2`` by name, and a removed release cannot be put back under its old id.
    Created as INGEST because that is the role that owns ``catalog_data`` tables
    in production and the role ``remove_release`` itself runs as. Returns the
    new release id and the partition name (which needs that id).
    """
    eng = create_catalog_engine(
        settings.config_for(CatalogRole.INGEST), pool_size=1, max_overflow=0
    )
    try:
        with eng.begin() as conn:
            release_id = conn.execute(
                text(
                    "INSERT INTO catalog_registry.catalog_release "
                    "(family_id, name, internal_schema_version, state, validation_status) "
                    "SELECT f.id, :n, 1, 'superseded', 'passed' "
                    "FROM catalog_registry.catalog_family f WHERE f.slug='apass' "
                    "RETURNING id"
                ),
                {"n": name},
            ).scalar_one()
            table = table_template.format(id=release_id)
            conn.execute(
                text(
                    f'CREATE TABLE catalog_data."{table}" PARTITION OF '
                    f"catalog_data.apass_source FOR VALUES IN ({release_id})"
                )
            )
            conn.execute(
                text(
                    "UPDATE catalog_registry.catalog_release "
                    "SET production_table = :p WHERE id = :i"
                ),
                {"p": f"catalog_data.{table}", "i": release_id},
            )
            conn.execute(
                text(
                    "INSERT INTO catalog_registry.ingestion_run "
                    "(release_id, status, stage) VALUES (:i, 'succeeded', 'attach')"
                ),
                {"i": release_id},
            )
        return release_id, table
    finally:
        eng.dispose()


def _forget_removable_release(settings, release_id: int, table: str) -> None:
    """Undo `_register_removable_release` if the test did not get that far."""
    eng = create_catalog_engine(
        settings.config_for(CatalogRole.INGEST), pool_size=1, max_overflow=0
    )
    try:
        with eng.begin() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS catalog_data."{table}"'))
            conn.execute(
                text("DELETE FROM catalog_registry.catalog_release WHERE id = :i"),
                {"i": release_id},
            )
    finally:
        eng.dispose()


@pytest.mark.parametrize(
    "table_template",
    [
        "apass_source_r{id}",  # the runner's own naming
        "apass_source_archive_r{id}",  # a partition that does not follow it
    ],
)
def test_remove_release_drops_the_partition_and_cascades(imported, table_template):
    """The detach/drop/delete path, which the refusal test returns before.

    The second parametrisation pins where the parent name comes from.
    ``remove_release`` used to rebuild it as ``tbl.rsplit("_r", 1)[0]``, which
    yields the nonexistent ``apass_source_archive`` here and leaves the
    partition attached; the parent is read from ``pg_inherits`` instead, in the
    query that already tests attachment.
    """
    name = f"TEST-REMOVE-{table_template}"
    release_id, partition = _register_removable_release(imported, name, table_template)
    try:
        report = remove_release(imported, "apass", name)
        assert report["production_table"] == f"catalog_data.{partition}"

        eng = _reader(imported)
        with eng.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT to_regclass(:t)"),
                    {"t": f'catalog_data."{partition}"'},
                ).scalar()
                is None
            ), "the partition survived remove_release"
            assert (
                conn.execute(
                    text("SELECT count(*) FROM catalog_registry.catalog_release WHERE id=:i"),
                    {"i": release_id},
                ).scalar()
                == 0
            )
            # ON DELETE CASCADE on ingestion_run.release_id: a removed release
            # must not leave orphaned run rows behind.
            assert (
                conn.execute(
                    text("SELECT count(*) FROM catalog_registry.ingestion_run WHERE release_id=:i"),
                    {"i": release_id},
                ).scalar()
                == 0
            )
        eng.dispose()
    finally:
        _forget_removable_release(imported, release_id, partition)


def test_idempotent_reimport_skips(imported):
    report = import_release(imported, "apass", "dr6")  # no replace, unchanged source
    assert report.skipped_reason is not None


def test_active_reimport_stays_active_and_swaps(imported):
    # Re-import the already-ACTIVE DR10 in place. The reworked path builds the
    # replacement detached then atomically swaps it, so the release must stay
    # ACTIVE and keep serving (no no-active-release window), the partition is
    # re-attached under the canonical name, and no detached build table is left
    # behind. Leaves the same end state (DR10 active, DR6 superseded), so it is
    # safe alongside the other fixture-shared tests.
    before = cone_search(imported, "apass", 100.0039, 4.861469, radius_deg=0.5)
    assert before  # DR10 active + queryable before the re-import

    report = import_release(imported, "apass", "dr10", replace=True, force=True)
    assert report.state == "active"
    assert report.activated is True

    eng = _reader(imported)
    with eng.connect() as conn:
        states = dict(
            conn.execute(
                text(
                    "SELECT r.name, r.state FROM catalog_registry.catalog_release r "
                    "JOIN catalog_registry.catalog_family f ON f.id=r.family_id "
                    "WHERE f.slug='apass'"
                )
            ).all()
        )
        parts = set(
            conn.execute(
                text(
                    "SELECT inhrelid::regclass::text FROM pg_inherits "
                    "WHERE inhparent='catalog_data.apass_source'::regclass"
                )
            ).scalars()
        )
        leftover = conn.execute(
            text(
                "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='catalog_data' AND c.relname='apass_source_r2_incoming'"
            )
        ).scalar()
    eng.dispose()

    assert states["DR10"] == "active"  # stayed ACTIVE across the in-place replace
    assert states["DR6"] == "superseded"
    assert any(p.endswith("apass_source_r2") for p in parts)  # re-attached canonically
    assert leftover is None  # the detached build table was renamed, not left behind

    # Data still present + identical after the swap.
    after = cone_search(imported, "apass", 100.0039, 4.861469, radius_deg=0.5)
    assert after and after[0]["native_id"] == before[0]["native_id"]


def test_staging_table_cleaned(imported):
    eng = _reader(imported)
    with eng.connect() as conn:
        staging = set(
            conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='catalog_staging'"
                )
            ).scalars()
        )
    eng.dispose()
    # The transient *_stg table is dropped after success; *_rejects is retained.
    assert not any(t.endswith("_stg") for t in staging)


def test_missing_source_fails_cleanly(imported, tmp_path):
    from skycat.ingestion import IngestionError

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(IngestionError):
        import_release(
            imported, "apass", "dr6", explicit_dir=str(empty), replace=True, force=True
        )


# --------------------------------------------------------------- Landolt / Stetson


def test_landolt_release_states(imported):
    """Two Landolt releases: 2009 active (newest), 1992 superseded but queryable."""
    eng = _reader(imported)
    with eng.connect() as conn:
        states = dict(
            conn.execute(
                text(
                    "SELECT r.name, r.state FROM catalog_registry.catalog_release r "
                    "JOIN catalog_registry.catalog_family f ON f.id=r.family_id "
                    "WHERE f.slug='landolt'"
                )
            ).all()
        )
    eng.dispose()
    assert states["2009"] == "active"
    assert states["1992"] == "superseded"


def test_landolt_explicit_releases_queryable(imported):
    # TPHE A is at RA 7.5375 deg, Dec -46.5228 in both releases.
    dr92 = cone_search(
        imported, "landolt", 7.5375, -46.5228, radius_deg=0.1, release="1992"
    )
    assert any(r["native_id"] == "TPHE A" for r in dr92)
    # Active (2009) resolves without an explicit release.
    active = cone_search(imported, "landolt", 7.5375, -46.5228, radius_deg=0.1)
    assert any(r["native_id"].startswith("TPhe") for r in active)
    # V + the five color indices are typed columns (U/B/R/I are derived downstream).
    row = next(r for r in dr92 if r["native_id"] == "TPHE A")
    assert row["johnson_v_mag"] == 14.651
    assert row["b_minus_v_mag"] == 0.793


def test_landolt_ra_wraparound(imported):
    # The RWRAP B fixture star sits at RA ≈ 359.958; a cone at RA 0.0 must find it.
    rows = cone_search(imported, "landolt", 0.0, -0.0375, radius_deg=0.2, release="1992")
    assert any(r["native_id"] == "RWRAP B" for r in rows)


def test_stetson_cone_and_field(imported):
    # E3 cluster fixture stars cluster near RA 138.8, Dec -77.1.
    rows = cone_search(imported, "stetson", 138.822, -77.112, radius_deg=0.5)
    assert rows and rows[0]["native_id"] == "4"
    assert rows[0]["cluster"] == "E3"
    # partial-band star: B/V/I present, U/R NULL (missing stays missing).
    assert rows[0]["johnson_b_mag"] == 19.457
    assert rows[0]["johnson_u_mag"] is None and rows[0]["cousins_r_mag"] is None


def test_stetson_native_id_repeats_across_clusters(imported):
    # Star "1741" appears in two clusters in the fixture (unique only within a field).
    rows = lookup_native_id(imported, "stetson", "1741")
    clusters = {r["cluster"] for r in rows}
    assert clusters == {"NGC6205", "NGC288"}


def test_stetson_cone_uses_spatial_index(imported):
    from skycat.spatial import degrees_to_meters

    eng = _reader(imported)
    radius_m = degrees_to_meters(0.5)
    with eng.connect() as conn:
        rid = conn.execute(
            text(
                "SELECT r.id FROM catalog_registry.catalog_release r "
                "JOIN catalog_registry.catalog_family f ON f.id=r.family_id "
                "WHERE f.slug='stetson' AND r.state='active'"
            )
        ).scalar()
        conn.exec_driver_sql("SET LOCAL enable_seqscan = off")
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                text(
                    f"EXPLAIN SELECT id FROM catalog_data.stetson_source "
                    f"WHERE release_id = {rid} AND ST_DWithin(geom, "
                    f"ST_SetSRID(ST_MakePoint(138.822, -77.112),4326)::geography, {radius_m}, false)"
                )
            ).all()
        )
    eng.dispose()
    assert "Index Scan" in plan or "Bitmap Index Scan" in plan or "gist" in plan.lower()


def test_landolt_batch_crossmatch(imported):
    inputs = [("a", 7.5375, -46.5228), ("z", 12.0, 80.0)]
    rows = batch_crossmatch(imported, "landolt", inputs, radius_deg=60 / 3600.0,
                            release="1992")
    by_id = {r["input_id"]: r for r in rows}
    assert by_id["a"]["matched"] and by_id["a"]["native_id"] == "TPHE A"
    assert not by_id["z"]["matched"]
