"""Catalog database initialization and (guarded) destructive reset.

``initialize_catalog_database`` is **non-destructive**: it enables PostGIS,
creates/refreshes roles + grants, ensures the schemas, and applies Alembic
migrations. It never drops anything and never touches ``sky``.

``reset_catalog_database`` is the explicit, guarded **dev-only** destructive
operation (drops the catalog schemas — not the database, not the volume, not the
source files).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from ..config import CatalogConfigError, CatalogRole, CatalogSettings
from ..constants import (
    ALL_SCHEMAS,
    FORBIDDEN_DATABASE_NAMES,
    ROLE_INGEST,
    ROLE_OWNER,
    ROLE_READER,
)
from .engine import create_catalog_engine, fetch_current_database
from .migrate import current_revision, upgrade
from .postgis import enable_postgis, verify_postgis
from .roles import (
    apply_existing_object_grants,
    apply_grants,
    ensure_roles,
    ensure_schemas_owned,
    grant_ingest_to_owner,
    reassign_data_objects_to_ingest,
)


@dataclass
class InitResult:
    database: str
    postgis_version: str
    roles_ensured: tuple[str, ...]
    schemas: tuple[str, ...]
    migrated_to: str | None


def _bootstrap_config(settings: CatalogSettings):
    """Best bootstrap identity: explicit bootstrap, else admin, else default."""
    if settings.bootstrap_user:
        return settings.config_for(CatalogRole.BOOTSTRAP)
    if settings.admin_user:
        return settings.config_for(CatalogRole.ADMIN)
    return settings.config_for(CatalogRole.DEFAULT)


def initialize_catalog_database(settings: CatalogSettings) -> InitResult:
    boot = _bootstrap_config(settings)
    boot.assert_not_primary_database()

    engine = create_catalog_engine(boot, pool_size=1, max_overflow=0)
    try:
        # 1. Verify target database is not the operational one.
        current_db = fetch_current_database(engine)
        if current_db.lower() in FORBIDDEN_DATABASE_NAMES:
            raise CatalogConfigError(
                f"Refusing to initialize catalog objects in database {current_db!r}."
            )

        with engine.begin() as conn:
            # 2. PostGIS (requires bootstrap/DBA privileges).
            pg_version = enable_postgis(conn)
            # 3. Operational roles.
            ensure_roles(
                conn,
                owner_password=settings.admin_password or None,
                ingest_password=settings.ingest_password or None,
                reader_password=settings.reader_password or None,
            )
            # 4. Schemas owned by the owner role.
            ensure_schemas_owned(conn)
            # 5. Grants + default privileges (before migrations).
            apply_grants(conn, database=current_db)
            # 5b. Let the owner manage ingest-owned data objects (see roles.py).
            grant_ingest_to_owner(conn)
    finally:
        engine.dispose()

    # 6. Migrations as the owner/migrator role.
    admin = settings.config_for(CatalogRole.ADMIN)
    admin.assert_not_primary_database()
    upgrade(admin, "head")

    # 7. Hand the data + staging objects to the ingest role, then catch-all
    #    grants over freshly-created objects + verify readiness.
    engine = create_catalog_engine(boot, pool_size=1, max_overflow=0)
    try:
        with engine.begin() as conn:
            reassign_data_objects_to_ingest(conn)
            apply_existing_object_grants(conn)
        with engine.connect() as conn:
            verify_postgis(conn)
            present = {
                row
                for row in conn.execute(
                    text("SELECT schema_name FROM information_schema.schemata")
                ).scalars()
            }
            missing = [s for s in ALL_SCHEMAS if s not in present]
            if missing:
                raise CatalogConfigError(f"Schemas missing after init: {missing}")
    finally:
        engine.dispose()

    rev = current_revision(admin)
    return InitResult(
        database=current_db,
        postgis_version=pg_version,
        roles_ensured=(ROLE_OWNER, ROLE_INGEST, ROLE_READER),
        schemas=ALL_SCHEMAS,
        migrated_to=rev,
    )


def reset_catalog_database(
    settings: CatalogSettings,
    *,
    force: bool = False,
    allow_production: bool = False,
) -> None:
    """DESTRUCTIVE (dev only): drop the catalog schemas.

    Drops ``catalog_data`` / ``catalog_staging`` / ``catalog_registry`` (CASCADE)
    — never the database, never the Docker volume, never the source files, never
    ``sky``. Re-run ``init`` afterwards to rebuild.
    """
    boot = _bootstrap_config(settings)
    boot.assert_not_primary_database()

    if not force:
        raise CatalogConfigError(
            "Refusing destructive catalog reset without force=True (--force)."
        )
    if boot.looks_production() and not allow_production:
        raise CatalogConfigError(
            f"Target {boot.target_summary()} looks production-like; refusing reset "
            "without an explicit production override (--allow-production)."
        )

    engine = create_catalog_engine(boot, pool_size=1, max_overflow=0)
    try:
        current_db = fetch_current_database(engine)
        if current_db.lower() in FORBIDDEN_DATABASE_NAMES:
            raise CatalogConfigError(
                f"Refusing destructive reset against database {current_db!r}."
            )
        with engine.begin() as conn:
            for (
                schema
            ) in ALL_SCHEMAS:  # registry last would orphan FKs; CASCADE handles order
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    finally:
        engine.dispose()
