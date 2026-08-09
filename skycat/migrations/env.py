"""Alembic environment for the catalog database.

* Target metadata is **only** ``CatalogBase.metadata``.
* The URL is resolved from ``SKYCAT_DB_*`` (admin/owner role) unless
  overridden with ``-x url=...`` / ``SKYCAT_ALEMBIC_URL``.
* Refuses to run against reserved PostgreSQL maintenance databases.
* The Alembic version table lives in the ``catalog_registry`` schema; the three
  catalog schemas are ensured to exist before migrations run so a fresh database
  bootstraps cleanly.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text

from skycat.config import CatalogDatabaseConfig, load_settings
from skycat.constants import ALL_SCHEMAS, CatalogRole
from skycat.database.base import CatalogBase
from skycat.constants import SCHEMA_REGISTRY

# Import the models so every table is attached to the metadata.
import skycat.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    try:
        # disable_existing_loggers=False, unlike Alembic's generated template.
        # The default is True, and it does not mean "reset the ini's loggers" —
        # it disables *every* logger already created in the process. In a
        # long-lived process that migrates and then imports (the test fixture,
        # and any service that calls `initialize_catalog_database`), that
        # silences `skycat.ingestion` for the rest of its life, taking the
        # structured phase events and the failure recorder's ERROR with it.
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    except Exception:  # pragma: no cover - logging is best-effort
        pass

target_metadata = CatalogBase.metadata


def _resolve_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    url = (
        x_args.get("url")
        or os.environ.get("SKYCAT_ALEMBIC_URL")
        or config.get_main_option("sqlalchemy.url")
    )
    if url:
        # Guard a directly-supplied URL too.
        name = url.rsplit("/", 1)[-1].split("?", 1)[0]
        CatalogDatabaseConfig(name=name).assert_not_reserved_database()
        return url
    settings = load_settings()
    cfg = settings.config_for(CatalogRole.ADMIN)
    cfg.assert_not_reserved_database()
    return cfg.url()


def _ensure_schemas(connection) -> None:
    for schema in ALL_SCHEMAS:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))


def run_migrations_offline() -> None:
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=SCHEMA_REGISTRY,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _resolve_url()
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        _ensure_schemas(connection)
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=SCHEMA_REGISTRY,
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
