"""Maintenance operations: table/index sizes, staging cleanup, release removal,
re-validation. Kept out of the runner so the CLI stays thin.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import CatalogConfigError, CatalogRole, CatalogSettings
from ..constants import (
    SCHEMA_DATA,
    SCHEMA_REGISTRY,
    SCHEMA_STAGING,
    CatalogReleaseState,
    ValidationStatus,
)
from ..database.engine import create_catalog_engine
from ..models.registry import CatalogRelease, ValidationSummary
from ..registry.releases import resolve_release
from ..validation import summarize, validate_production


def table_sizes(settings: CatalogSettings) -> list[dict]:
    cfg = settings.config_for(CatalogRole.READER)
    cfg.assert_not_primary_database()
    engine = create_catalog_engine(cfg, pool_size=1, max_overflow=0)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                """
                SELECT n.nspname AS schema, c.relname AS table,
                       pg_total_relation_size(c.oid) AS total_bytes,
                       pg_indexes_size(c.oid) AS index_bytes,
                       c.reltuples::bigint AS approx_rows
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = ANY(:s) AND c.relkind IN ('r','p')
                ORDER BY pg_total_relation_size(c.oid) DESC
                """
            ), {"s": [SCHEMA_DATA, SCHEMA_REGISTRY, SCHEMA_STAGING]}).mappings()
            return [dict(r) for r in rows]
    finally:
        engine.dispose()


def clean_staging(settings: CatalogSettings) -> list[str]:
    """Drop all staging tables. Never touches production or source files."""
    cfg = settings.config_for(CatalogRole.INGEST)
    cfg.assert_not_primary_database()
    engine = create_catalog_engine(cfg, pool_size=1, max_overflow=0)
    dropped: list[str] = []
    try:
        with engine.begin() as conn:
            names = conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = :s"
            ), {"s": SCHEMA_STAGING}).scalars().all()
            for name in names:
                conn.execute(text(f'DROP TABLE IF EXISTS {SCHEMA_STAGING}."{name}"'))
                dropped.append(f"{SCHEMA_STAGING}.{name}")
        return dropped
    finally:
        engine.dispose()


def remove_release(
    settings: CatalogSettings, family_slug: str, release_ref: str, *, force: bool = False
) -> dict:
    """Remove an INACTIVE release (drops its partition + registry rows).

    Refuses to remove an ACTIVE release unless ``force`` (which also detaches it).
    """
    cfg = settings.config_for(CatalogRole.INGEST)
    cfg.assert_not_primary_database()
    engine = create_catalog_engine(cfg, pool_size=1, max_overflow=0)
    try:
        with Session(engine) as session:
            release = resolve_release(session, family_slug, release_ref)
            if release is None:
                raise CatalogConfigError(f"No release {family_slug}/{release_ref}")
            if release.state == CatalogReleaseState.ACTIVE.value and not force:
                raise CatalogConfigError(
                    f"Release {release.name} is ACTIVE; deactivate first or use --force."
                )
            prod = release.production_table
            release_id = release.id
            name = release.name

        with engine.begin() as conn:
            if prod:
                schema, _, tbl = prod.partition(".")
                attached = conn.execute(text(
                    "SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:s AND c.relname=:t"
                ), {"s": schema, "t": tbl}).scalar()
                if attached:
                    family_def_table = tbl.rsplit("_r", 1)[0]
                    conn.execute(text(
                        f'ALTER TABLE {schema}."{family_def_table}" DETACH PARTITION {schema}."{tbl}"'
                    ))
                conn.execute(text(f'DROP TABLE IF EXISTS {schema}."{tbl}"'))
        with Session(engine) as session:
            release = session.get(CatalogRelease, release_id)
            if release is not None:
                session.delete(release)
                session.commit()
        return {"removed": f"{family_slug}/{name}", "production_table": prod}
    finally:
        engine.dispose()


def revalidate_release(settings: CatalogSettings, family_slug: str, release_ref: str) -> dict:
    """Re-run production validation (rows present, spatial index, coord ranges)
    on a release's partition and record a fresh ValidationSummary."""
    cfg = settings.config_for(CatalogRole.INGEST)
    cfg.assert_not_primary_database()
    engine = create_catalog_engine(cfg, pool_size=1, max_overflow=0)
    try:
        with Session(engine) as session:
            release = resolve_release(session, family_slug, release_ref)
            if release is None or not release.production_table:
                raise CatalogConfigError(f"No imported release {family_slug}/{release_ref}")
            schema, _, tbl = release.production_table.partition(".")
            release_id = release.id

        with engine.connect() as conn:
            checks = validate_production(conn, schema, tbl)
            bad_ra = conn.execute(text(
                f'SELECT count(*) FROM {schema}."{tbl}" WHERE ra_deg < 0 OR ra_deg >= 360'
            )).scalar() or 0
            bad_dec = conn.execute(text(
                f'SELECT count(*) FROM {schema}."{tbl}" WHERE dec_deg < -90 OR dec_deg > 90'
            )).scalar() or 0
        from ..validation.common import CRITICAL, Check
        checks.append(Check("production_ra_range", CRITICAL, bad_ra == 0, f"{bad_ra} bad RA"))
        checks.append(Check("production_dec_range", CRITICAL, bad_dec == 0, f"{bad_dec} bad Dec"))
        status = summarize(checks)

        with Session(engine) as session:
            session.add(ValidationSummary(
                release_id=release_id, status=status.value,
                checks=[c.to_dict() for c in checks],
            ))
            release = session.get(CatalogRelease, release_id)
            release.validation_status = status.value
            release.validated_at = datetime.now(timezone.utc)
            session.commit()
        return {"status": status.value, "checks": [c.to_dict() for c in checks]}
    finally:
        engine.dispose()
