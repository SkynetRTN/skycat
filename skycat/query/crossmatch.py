"""Batch spatial crossmatch.

Matches many input coordinates against a catalog release in a single database
round trip: the inputs are streamed (COPY) into a TEMP table with a GENERATED
geography column, then a LATERAL join uses the catalog's GiST index
(``geom <-> point`` KNN ordering + ``ST_DWithin`` radius cap) to find the
nearest match(es) per input. No per-input round trips, no Python distance maths.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from ..config import CatalogRole, CatalogSettings
from ..constants import POSTGIS_SPHERE_RADIUS_M, SCHEMA_DATA
from ..database.base import CatalogBase
from ..database.engine import create_catalog_engine, driver_connection
from ..spatial import (
    GEOM_GENERATED_EXPR,
    degrees_to_meters,
    is_valid_dec,
    is_valid_ra,
)
from .cone import ResolvedRelease, resolve_release_for_query

logger = logging.getLogger(__name__)


def batch_crossmatch(
    settings: CatalogSettings,
    family_slug: str,
    inputs: Iterable[tuple[str, float, float]],
    *,
    radius_deg: float,
    release: str | None = None,
    nearest_only: bool = True,
    max_candidates: int = 1,
    role: CatalogRole = CatalogRole.READER,
    engine: Engine | None = None,
    resolved: ResolvedRelease | None = None,
) -> list[dict]:
    """Crossmatch ``inputs`` (``(input_id, ra_deg, dec_deg)``) against a release.

    Returns one dict per (input, candidate); inputs with no match within
    ``radius_deg`` yield a single ``matched=False`` row. Ordering is
    deterministic: by ``input_id`` then ascending separation.

    Pass ``engine`` to reuse a caller-managed (pooled) engine; it is left open.
    When omitted, a short-lived engine is created and disposed here. Pass
    ``resolved`` to skip the registry lookup; it takes precedence over
    ``release``.
    """
    own_engine = engine is None
    if engine is None:
        cfg = settings.config_for(role)
        cfg.assert_not_reserved_database()
        engine = create_catalog_engine(cfg, pool_size=1, max_overflow=1)
    limit = 1 if nearest_only else max(1, max_candidates)
    radius_m = degrees_to_meters(radius_deg)
    try:
        if resolved is None:
            with Session(engine) as session:
                resolved = resolve_release_for_query(session, family_slug, release)
        table = CatalogBase.metadata.tables[f"{SCHEMA_DATA}.{resolved.data_table}"]
        cat_cols = [c.name for c in table.c if c.name != "geom"]
        inner_cols = ", ".join(f'c."{n}"' for n in cat_cols)
        outer_cols = ", ".join(f'm."{n}"' for n in cat_cols)

        with engine.begin() as conn:
            conn.exec_driver_sql(
                f"""
                CREATE TEMP TABLE _xm_inputs (
                    input_id text,
                    ra_deg double precision,
                    dec_deg double precision,
                    qgeom geography(Point,4326) GENERATED ALWAYS AS ({GEOM_GENERATED_EXPR}) STORED
                ) ON COMMIT DROP
                """
            )
            driver = driver_connection(conn)
            n_inputs = 0
            invalid_ids: list[str] = []
            with driver.cursor() as cur:
                with cur.copy(
                    "COPY _xm_inputs (input_id, ra_deg, dec_deg) FROM STDIN"
                ) as cp:
                    for input_id, ra, dec in inputs:
                        ra_f, dec_f = float(ra), float(dec)
                        # Validate BEFORE the COPY: an out-of-range Dec makes the
                        # generated geography cast fail at COPY time and aborts the
                        # entire batch transaction (opaque driver error, all
                        # results lost); a >360 RA silently returns a wrong nearest
                        # match. Skip-and-mark instead so one bad row can't sink the
                        # batch — the skipped inputs come back as matched=False below.
                        if not (is_valid_ra(ra_f) and is_valid_dec(dec_f)):
                            invalid_ids.append(str(input_id))
                            continue
                        cp.write_row((str(input_id), ra_f, dec_f))
                        n_inputs += 1
            if invalid_ids:
                logger.warning(
                    "batch_crossmatch: skipped %d input(s) with out-of-range "
                    "coordinates (returned as matched=False); first: %s",
                    len(invalid_ids),
                    invalid_ids[:5],
                )

            sql = text(
                f"""
                SELECT q.input_id,
                       (m.separation_deg IS NOT NULL) AS matched,
                       {outer_cols},
                       m.separation_deg
                FROM _xm_inputs q
                LEFT JOIN LATERAL (
                    SELECT {inner_cols},
                           ST_Distance(c.geom, q.qgeom, false) / :r * (180.0 / pi()) AS separation_deg
                    FROM {SCHEMA_DATA}."{resolved.data_table}" c
                    WHERE c.release_id = :rid
                      AND ST_DWithin(c.geom, q.qgeom, :radm, false)
                    ORDER BY c.geom <-> q.qgeom
                    LIMIT :lim
                ) m ON true
                ORDER BY q.input_id, m.separation_deg NULLS LAST
                """
            )
            rows = conn.execute(
                sql,
                {
                    "r": POSTGIS_SPHERE_RADIUS_M,
                    "rid": resolved.release_id,
                    "radm": radius_m,
                    "lim": limit,
                },
            ).mappings()
            results = [dict(row) for row in rows]
            if invalid_ids:
                # Represent every input: emit a matched=False row (catalog columns
                # NULL) for each skipped input, same shape as a genuine no-match
                # row, then re-sort so the by-input_id ordering contract holds.
                null_cols = {n: None for n in cat_cols}
                results.extend(
                    {"input_id": iid, "matched": False, **null_cols, "separation_deg": None}
                    for iid in invalid_ids
                )
                results.sort(key=lambda row: row["input_id"])
            return results
    finally:
        if own_engine:
            engine.dispose()
