"""PostGIS cone search over catalog releases.

All spatial filtering happens in the database via the GiST-indexed ``geom``
geography column — complete catalog tables are never loaded into Python. The
search is spherical (ST_DWithin/ST_Distance with ``use_spheroid => false``), so
RA wraparound at 0/360 and the celestial poles are handled correctly and the
returned ``separation_deg`` is a true angular separation.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from ..config import CatalogRole, CatalogSettings
from ..constants import SCHEMA_DATA
from ..database.base import CatalogBase
from ..database.engine import create_catalog_engine
from ..registry.catalog_defs import get_family_def
from ..registry.releases import resolve_active_release, resolve_release
from ..spatial import separation_deg_expr, validate_radec, within_radius


class CatalogQueryError(RuntimeError):
    pass


@dataclass
class ResolvedRelease:
    family_slug: str
    data_table: str
    release_id: int
    release_name: str
    state: str


def radius_to_deg(
    *, radius_deg: float | None = None, radius_arcmin: float | None = None,
    radius_arcsec: float | None = None,
) -> float:
    provided = [r for r in (radius_deg, radius_arcmin, radius_arcsec) if r is not None]
    if len(provided) != 1:
        raise CatalogQueryError("Specify exactly one of radius_deg / radius_arcmin / radius_arcsec")
    if radius_deg is not None:
        return radius_deg
    if radius_arcmin is not None:
        return radius_arcmin / 60.0
    return radius_arcsec / 3600.0


def _data_table(data_table: str):
    return CatalogBase.metadata.tables[f"{SCHEMA_DATA}.{data_table}"]


def resolve_release_for_query(
    session: Session, family_slug: str, release: str | None
) -> ResolvedRelease:
    fam_def = get_family_def(family_slug)
    if fam_def is None:
        raise CatalogQueryError(f"Unknown family {family_slug!r}")
    rel = (
        resolve_release(session, family_slug, release)
        if release else resolve_active_release(session, family_slug)
    )
    if rel is None:
        raise CatalogQueryError(
            f"No {'matching' if release else 'active'} release for {family_slug!r}"
            + (f" ({release!r})" if release else "")
        )
    return ResolvedRelease(
        family_slug=family_slug, data_table=fam_def.data_table,
        release_id=rel.id, release_name=rel.name, state=str(rel.state),
    )


def cone_search(
    settings: CatalogSettings,
    family_slug: str,
    ra_deg: float,
    dec_deg: float,
    *,
    radius_deg: float,
    release: str | None = None,
    limit: int = 100,
    mag_band: str | None = None,
    mag_min: float | None = None,
    mag_max: float | None = None,
    quality_filter: str | None = None,
    role: CatalogRole = CatalogRole.READER,
    engine: Engine | None = None,
) -> list[dict]:
    """Return rows within ``radius_deg`` of (ra, dec), nearest first.

    ``mag_band`` filters on a typed magnitude column (e.g. ``johnson_v_mag``);
    ``quality_filter`` is a raw SQL boolean over the row (advanced use).
    """
    validate_radec(ra_deg, dec_deg)
    own_engine = engine is None
    if engine is None:
        cfg = settings.config_for(role)
        cfg.assert_not_primary_database()
        engine = create_catalog_engine(cfg, pool_size=2, max_overflow=2)
    try:
        with Session(engine) as session:
            resolved = resolve_release_for_query(session, family_slug, release)
        table = _data_table(resolved.data_table)
        out_cols = [c for c in table.c if c.name != "geom"]
        sep = separation_deg_expr(table.c.geom, ra_deg, dec_deg).label("separation_deg")
        stmt = (
            select(*out_cols, sep)
            .where(table.c.release_id == resolved.release_id)
            .where(within_radius(table.c.geom, ra_deg, dec_deg, radius_deg))
        )
        if mag_band:
            col = table.c.get(mag_band)
            if col is None:
                raise CatalogQueryError(f"Unknown magnitude column {mag_band!r}")
            if mag_min is not None:
                stmt = stmt.where(col >= mag_min)
            if mag_max is not None:
                stmt = stmt.where(col <= mag_max)
        if quality_filter:
            stmt = stmt.where(text(quality_filter))
        stmt = stmt.order_by(sep).limit(limit)
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(stmt).mappings()]
    finally:
        if own_engine:
            engine.dispose()


def lookup_native_id(
    settings: CatalogSettings,
    family_slug: str,
    native_id: str,
    *,
    release: str | None = None,
    limit: int = 100,
    role: CatalogRole = CatalogRole.READER,
) -> list[dict]:
    cfg = settings.config_for(role)
    cfg.assert_not_primary_database()
    engine = create_catalog_engine(cfg, pool_size=1, max_overflow=1)
    try:
        with Session(engine) as session:
            resolved = resolve_release_for_query(session, family_slug, release)
        table = _data_table(resolved.data_table)
        out_cols = [c for c in table.c if c.name != "geom"]
        stmt = (
            select(*out_cols)
            .where(table.c.release_id == resolved.release_id)
            .where(table.c.native_id == native_id)
            .limit(limit)
        )
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(stmt).mappings()]
    finally:
        engine.dispose()


def cone_search_plan(
    settings: CatalogSettings, family_slug: str, ra_deg: float, dec_deg: float,
    *, radius_deg: float, release: str | None = None, analyze: bool = False,
) -> str:
    """Return the EXPLAIN [ANALYZE] plan — used to demonstrate index usage."""
    cfg = settings.config_for(CatalogRole.READER)
    cfg.assert_not_primary_database()
    engine = create_catalog_engine(cfg, pool_size=1, max_overflow=1)
    try:
        with Session(engine) as session:
            resolved = resolve_release_for_query(session, family_slug, release)
        table = _data_table(resolved.data_table)
        sep = separation_deg_expr(table.c.geom, ra_deg, dec_deg).label("separation_deg")
        stmt = (
            select(table.c.id, sep)
            .where(table.c.release_id == resolved.release_id)
            .where(within_radius(table.c.geom, ra_deg, dec_deg, radius_deg))
            .order_by(sep).limit(50)
        )
        compiled = stmt.compile(engine, compile_kwargs={"literal_binds": True})
        prefix = "EXPLAIN (ANALYZE, BUFFERS) " if analyze else "EXPLAIN "
        with engine.connect() as conn:
            rows = conn.execute(text(prefix + str(compiled))).all()
        return "\n".join(r[0] for r in rows)
    finally:
        engine.dispose()
