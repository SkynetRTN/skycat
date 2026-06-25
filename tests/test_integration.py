"""PostGIS integration tests (marker: postgis).

Exercise the real database path: schemas, registry, release lifecycle,
spatial cone search (wraparound + poles + separation), index usage, native
lookup, batch crossmatch, role permissions, and data-safety guards. Requires a
reachable catalog PostGIS database (skipped otherwise — see conftest).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from skycat.config import CatalogConfigError, CatalogRole
from skycat.constants import ALL_SCHEMAS
from skycat.database.engine import create_catalog_engine
from skycat.ingestion import import_release
from skycat.ingestion.maintenance import remove_release
from skycat.query import batch_crossmatch, cone_search, lookup_native_id
from skycat.spatial import angular_separation_deg

pytestmark = pytest.mark.postgis


def _reader(settings):
    return create_catalog_engine(
        settings.config_for(CatalogRole.READER), pool_size=1, max_overflow=0
    )


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


def test_idempotent_reimport_skips(imported):
    report = import_release(imported, "apass", "dr6")  # no replace, unchanged source
    assert report.skipped_reason is not None


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
