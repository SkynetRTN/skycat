"""PostGIS cone search over catalog releases.

All spatial filtering happens in the database via the GiST-indexed ``geom``
geography column — complete catalog tables are never loaded into Python. The
search is spherical (ST_DWithin/ST_Distance with ``use_spheroid => false``), so
RA wraparound at 0/360 and the celestial poles are handled correctly and the
returned ``separation_deg`` is a true angular separation.
"""

from __future__ import annotations

import operator
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

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


#: The only comparison operators a quality filter may use. Membership is checked
#: before the clause is built, so a caller-supplied operator string never
#: reaches the SQL.
_QUALITY_OPERATORS = {
    "=": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


@dataclass(frozen=True)
class QualityFilter:
    """A ``column <op> value`` predicate over a release's data table.

    Both identifiers are validated against allow-lists — ``column`` must name a
    real non-spatial column of that table, ``op`` must be one of the six
    comparisons above — and ``value`` is bound as a parameter rather than
    interpolated. Callers may therefore build these from untrusted input.
    """

    column: str
    op: str
    value: float | int | str | bool


#: Python types a bound value may take, per the column's own Python type.
#: Widened exactly where PostgreSQL's comparison is: an ``int`` is a fine bound
#: for a ``double precision`` column. ``bool`` is kept out of the numeric sets on
#: purpose — it is an ``int`` subclass in Python and not a number in SQL.
_COMPATIBLE_VALUE_TYPES: dict[type, tuple[type, ...]] = {
    int: (int,),
    float: (int, float, Decimal),
    Decimal: (int, float, Decimal),
    str: (str,),
    bool: (bool,),
}


def _python_type(col) -> type | None:
    """The column's Python type, or ``None`` when it has no single one.

    ``JSONB`` and the geography types are the ``None`` cases here.
    """
    try:
        return col.type.python_type
    except NotImplementedError:
        return None


def _value_is_comparable(py_type: type, value: object) -> bool:
    allowed = _COMPATIBLE_VALUE_TYPES[py_type]
    if isinstance(value, bool) and bool not in allowed:
        return False
    return isinstance(value, allowed)


def _quality_clause(table, qf: QualityFilter):
    """Validate one filter against ``table`` and return its SQLAlchemy clause."""
    if qf.column == "geom":
        raise CatalogQueryError("Quality filters cannot target the spatial column 'geom'")
    col = table.c.get(qf.column)
    if col is None:
        raise CatalogQueryError(f"Unknown quality-filter column {qf.column!r}")
    try:
        apply_op = _QUALITY_OPERATORS[qf.op]
    except KeyError:
        raise CatalogQueryError(
            f"Unsupported quality-filter operator {qf.op!r}; allowed: "
            + ", ".join(sorted(_QUALITY_OPERATORS))
        ) from None
    # The value is bound, never interpolated — but binding a string against a
    # double precision column still fails, in the driver, as a ProgrammingError.
    # The docstring above invites untrusted input, so the mismatch is the
    # caller's error and gets the caller's error type.
    py_type = _python_type(col)
    if py_type is None or py_type not in _COMPATIBLE_VALUE_TYPES:
        raise CatalogQueryError(
            f"Quality filters are not supported on column {qf.column!r} "
            f"({col.type}); only numeric, text and boolean columns compare"
        )
    if not _value_is_comparable(py_type, qf.value):
        raise CatalogQueryError(
            f"Quality-filter value {qf.value!r} is a {type(qf.value).__name__}, but "
            f"column {qf.column!r} is {col.type}"
        )
    return apply_op(col, qf.value)


def _validate_centre(ra_deg: float, dec_deg: float) -> None:
    """Range-check the search centre, in this layer's error type.

    ``skycat.spatial`` keeps its bare ``ValueError``: it is dependency-free and
    shared with the parsers, where a coordinate out of range is a data defect
    rather than a query. ``api-stability.md`` promises callers of the query API
    ``CatalogQueryError``, so the translation happens at this boundary.
    """
    try:
        validate_radec(ra_deg, dec_deg)
    except ValueError as exc:
        raise CatalogQueryError(str(exc)) from exc


def _validate_limit(limit: int) -> None:
    """``LIMIT 0`` is a legitimate query; a negative one is a caller's mistake.

    PostgreSQL answers it with ``DataError: LIMIT must not be negative`` after a
    round trip. Refusing it here costs nothing and keeps the promised type.
    """
    if limit < 0:
        raise CatalogQueryError(f"limit must be >= 0, got {limit}")


def _order_by_clause(table, order_by: str):
    """Validate ``order_by`` against ``table`` and return its ordering clause.

    Ascending on a magnitude column is brightest-first, which is what a capped
    cone search almost always wants: nearest-N and brightest-N return different
    star sets in a dense field, and many workflows care about the brightest.
    Rows with no value sort last rather than displacing real matches.
    """
    if order_by == "geom":
        raise CatalogQueryError("Cannot order by the spatial column 'geom'")
    col = table.c.get(order_by)
    if col is None:
        raise CatalogQueryError(f"Unknown order-by column {order_by!r}")
    if _python_type(col) not in (int, float, Decimal):
        raise CatalogQueryError(
            f"Order-by column {order_by!r} is not numeric; ordering is only "
            "supported on numeric columns (e.g. johnson_v_mag)"
        )
    return col.asc().nulls_last()


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
    # (value, arcunits-per-degree) pairs, so the "exactly one" check and the
    # conversion read off the same list — no branch can outlive its guard.
    provided = [
        (value, per_degree)
        for value, per_degree in (
            (radius_deg, 1.0),
            (radius_arcmin, 60.0),
            (radius_arcsec, 3600.0),
        )
        if value is not None
    ]
    if len(provided) != 1:
        raise CatalogQueryError("Specify exactly one of radius_deg / radius_arcmin / radius_arcsec")
    value, per_degree = provided[0]
    return value / per_degree


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
    order_by: str | None = None,
    quality_filter: Sequence[QualityFilter] | None = None,
    role: CatalogRole = CatalogRole.READER,
    engine: Engine | None = None,
    resolved: ResolvedRelease | None = None,
) -> list[dict]:
    """Return rows within ``radius_deg`` of (ra, dec), nearest first by default.

    ``mag_band`` filters on a typed magnitude column (e.g. ``johnson_v_mag``);
    ``quality_filter`` is a sequence of :class:`QualityFilter` predicates, each
    ANDed onto the query after its column and operator are validated.

    ``order_by`` names a numeric column to sort ascending (NULLs last, angular
    separation as tiebreak) instead of sorting by separation. Passing a
    magnitude column gives brightest-first selection, so a ``limit`` keeps the
    brightest stars in the cone rather than the ones nearest its center.

    Pass ``resolved`` to skip the registry lookup when the caller already knows
    the release (see :class:`skycat.client.CatalogReader`, which caches it); it
    takes precedence over ``release``.
    """
    _validate_centre(ra_deg, dec_deg)
    _validate_limit(limit)
    own_engine = engine is None
    if engine is None:
        cfg = settings.config_for(role)
        cfg.assert_not_reserved_database()
        engine = create_catalog_engine(cfg, pool_size=2, max_overflow=2)
    try:
        if resolved is None:
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
        for qf in quality_filter or ():
            stmt = stmt.where(_quality_clause(table, qf))
        order = (
            (_order_by_clause(table, order_by), sep) if order_by else (sep,)
        )
        stmt = stmt.order_by(*order).limit(limit)
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
    engine: Engine | None = None,
    resolved: ResolvedRelease | None = None,
) -> list[dict]:
    """Return rows whose ``native_id`` matches, for the resolved release.

    Pass ``engine`` to reuse a caller-managed (pooled) engine; it is left open.
    When omitted, a short-lived engine is created and disposed here. Pass
    ``resolved`` to skip the registry lookup; it takes precedence over
    ``release``.
    """
    _validate_limit(limit)
    own_engine = engine is None
    if engine is None:
        cfg = settings.config_for(role)
        cfg.assert_not_reserved_database()
        engine = create_catalog_engine(cfg, pool_size=1, max_overflow=1)
    try:
        if resolved is None:
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
        if own_engine:
            engine.dispose()


def cone_search_plan(
    settings: CatalogSettings, family_slug: str, ra_deg: float, dec_deg: float,
    *, radius_deg: float, release: str | None = None, order_by: str | None = None,
    limit: int = 50, analyze: bool = False,
) -> str:
    """Return the EXPLAIN [ANALYZE] plan — used to demonstrate index usage.

    Mirrors :func:`cone_search`'s ordering: the two orderings produce materially
    different plans (a magnitude sort adds a top-N sort over the cone's candidate
    rows), so explaining a nearest-first query would misrepresent what a
    brightest-first one actually does.
    """
    _validate_centre(ra_deg, dec_deg)
    _validate_limit(limit)
    cfg = settings.config_for(CatalogRole.READER)
    cfg.assert_not_reserved_database()
    engine = create_catalog_engine(cfg, pool_size=1, max_overflow=1)
    try:
        with Session(engine) as session:
            resolved = resolve_release_for_query(session, family_slug, release)
        table = _data_table(resolved.data_table)
        sep = separation_deg_expr(table.c.geom, ra_deg, dec_deg).label("separation_deg")
        order = (
            (_order_by_clause(table, order_by), sep) if order_by else (sep,)
        )
        stmt = (
            select(table.c.id, sep)
            .where(table.c.release_id == resolved.release_id)
            .where(within_radius(table.c.geom, ra_deg, dec_deg, radius_deg))
            .order_by(*order).limit(limit)
        )
        compiled = stmt.compile(engine, compile_kwargs={"literal_binds": True})
        prefix = "EXPLAIN (ANALYZE, BUFFERS) " if analyze else "EXPLAIN "
        with engine.connect() as conn:
            rows = conn.execute(text(prefix + str(compiled))).all()
        return "\n".join(r[0] for r in rows)
    finally:
        engine.dispose()
