"""CatalogReader facade (marker: postgis).

The two behaviours the facade exists to add — active-release TTL caching and a
default statement timeout — are proven here rather than asserted at: registry
round trips are counted off the engine, and the timeout is read back from the
session.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import event

from skycat.client import DEFAULT_STATEMENT_TIMEOUT_MS, CatalogReader
from skycat.query import CatalogQueryError, cone_search

pytestmark = pytest.mark.postgis

CENTER = (100.0039, 4.861469)


@contextmanager
def registry_queries(engine):
    """Collect statements that hit the release registry (the cached lookup)."""
    seen: list[str] = []

    def _before(_conn, _cursor, statement, _params, _context, _many):
        if "catalog_release" in statement:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", _before)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", _before)


@pytest.fixture
def reader(imported):
    with CatalogReader(imported) as r:
        yield r


def test_cone_matches_the_module_function(reader, imported):
    via_facade = reader.cone("apass", *CENTER, radius_deg=0.5)
    direct = cone_search(imported, "apass", *CENTER, radius_deg=0.5)
    assert [r["native_id"] for r in via_facade] == [r["native_id"] for r in direct]


def test_cone_order_by_brightest(reader):
    rows = reader.cone(
        "apass", *CENTER, radius_deg=0.5, limit=3, order_by="johnson_v_mag"
    )
    mags = [r["johnson_v_mag"] for r in rows]
    assert mags == sorted(mags)
    assert rows[0]["native_id"] == "090-0000004"


def test_radius_units_are_equivalent(reader):
    by_deg = reader.cone("apass", *CENTER, radius_deg=0.5)
    by_arcmin = reader.cone("apass", *CENTER, radius_arcmin=30.0)
    assert [r["native_id"] for r in by_deg] == [r["native_id"] for r in by_arcmin]


def test_radius_must_be_given_exactly_once(reader):
    with pytest.raises(CatalogQueryError, match="exactly one"):
        reader.cone("apass", *CENTER)
    with pytest.raises(CatalogQueryError, match="exactly one"):
        reader.cone("apass", *CENTER, radius_deg=0.5, radius_arcmin=30.0)


def test_crossmatch_and_lookup(reader):
    rows = reader.crossmatch(
        "apass", [("a", *CENTER), ("b", 12.0, 80.0)], radius_arcsec=30.0
    )
    by_id = {r["input_id"]: r for r in rows}
    assert by_id["a"]["matched"] and by_id["a"]["native_id"] == "090-0000001"
    assert not by_id["b"]["matched"]

    assert reader.lookup("apass", "090-0000001")[0]["ra_deg"] == pytest.approx(100.0039)


# ----------------------------------------------------------------- TTL cache --
def test_active_release_is_cached_off_the_hot_path(reader):
    reader.cone("apass", *CENTER, radius_deg=0.5, limit=1)  # warm the cache
    with registry_queries(reader.engine) as seen:
        for _ in range(3):
            reader.cone("apass", *CENTER, radius_deg=0.5, limit=1)
    assert seen == [], "cached reader still resolved the release from the registry"


def test_zero_ttl_resolves_every_call(imported):
    with CatalogReader(imported, release_cache_ttl_s=0) as r:
        r.active_release("apass")
        with registry_queries(r.engine) as seen:
            r.active_release("apass")
            r.active_release("apass")
    assert len(seen) == 2


def test_invalidate_forces_a_refresh(reader):
    reader.active_release("apass")
    with registry_queries(reader.engine) as seen:
        reader.active_release("apass")
        assert seen == []  # served from cache
        reader.invalidate("apass")
        reader.active_release("apass")
    assert len(seen) == 1


def test_cache_is_per_family(reader):
    reader.active_release("apass")
    with registry_queries(reader.engine) as seen:
        reader.active_release("vsx")  # different family: must not hit apass's entry
    assert len(seen) == 1


def test_active_release_resolves_the_active_one(reader):
    assert reader.active_release("apass").release_name == "DR10"
    assert reader.active_release("landolt").release_name == "2009"


def test_explicit_release_bypasses_the_cache(reader):
    """--release is an ops/parity path; it must never be served from the cache."""
    assert reader.active_release("landolt").release_name == "2009"
    # TPHE A is at RA 7.5375, Dec -46.5228 in the 1992 release.
    rows = reader.cone("landolt", 7.5375, -46.5228, radius_deg=0.1, release="1992")
    assert any(r["native_id"] == "TPHE A" for r in rows)
    # ...and the cached active release is untouched by that query.
    assert reader.active_release("landolt").release_name == "2009"


def test_unknown_family_raises(reader):
    with pytest.raises(CatalogQueryError, match="Unknown family"):
        reader.active_release("nosuch")


# --------------------------------------------------------- statement timeout --
def test_default_statement_timeout_is_applied(reader):
    with reader.engine.connect() as conn:
        timeout = conn.exec_driver_sql("SHOW statement_timeout").scalar()
    assert timeout == "30s"  # DEFAULT_STATEMENT_TIMEOUT_MS
    assert DEFAULT_STATEMENT_TIMEOUT_MS == 30_000


def test_statement_timeout_can_be_overridden(imported):
    with CatalogReader(imported, statement_timeout_ms=1500) as r:
        with r.engine.connect() as conn:
            assert conn.exec_driver_sql("SHOW statement_timeout").scalar() == "1500ms"


def test_configured_timeout_wins_over_the_default(imported):
    import dataclasses

    configured = dataclasses.replace(
        imported, base=dataclasses.replace(imported.base, statement_timeout_ms=2500)
    )
    with CatalogReader(configured) as r:
        with r.engine.connect() as conn:
            assert conn.exec_driver_sql("SHOW statement_timeout").scalar() == "2500ms"


def test_timeout_actually_cancels_a_pathological_query(imported):
    from sqlalchemy.exc import DBAPIError

    with CatalogReader(imported, statement_timeout_ms=250) as r:
        with pytest.raises(DBAPIError), r.engine.connect() as conn:
            conn.exec_driver_sql("SELECT pg_sleep(5)")


# --------------------------------------------------------------------- life --
def test_engine_is_lazy_and_pooled(imported):
    r = CatalogReader(imported)
    assert r._engine is None  # not built until first use
    first = r.engine
    assert r.engine is first  # one shared pooled engine
    r.close()
    assert r._engine is None
