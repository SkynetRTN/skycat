"""Programmatic Alembic driver for the catalog database.

Used by ``init`` and the CLI so migrations run the same way everywhere
(host tools, Compose jobs, Kubernetes jobs) without shelling out to the
``alembic`` binary.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from ..config import CatalogDatabaseConfig
from ..constants import SCHEMA_REGISTRY

_PACKAGE_DIR = Path(__file__).resolve().parents[1]  # skycat/
_PROJECT_DIR = _PACKAGE_DIR.parent
_INI = _PROJECT_DIR / "alembic.ini"
_SCRIPT_LOCATION = _PACKAGE_DIR / "migrations"


def _ini_literal(value: str) -> str:
    """Escape ``value`` so configparser stores it verbatim.

    ``Config.set_main_option`` writes through ``configparser``, whose
    ``BasicInterpolation`` rejects any ``%`` that is not ``%%`` or ``%(name)s``.
    ``CatalogDatabaseConfig.url()`` percent-encodes the password, so ``@``, ``!``,
    ``#``, ``:``, ``$``, ``&``, ``+``, a space, or a literal ``%`` — most of what
    a password generator emits — used to raise ``ValueError`` from configparser
    instead of running a migration, taking out ``init``, ``migrate``,
    ``migrate-status``, the ``skycat-migrate`` Job, and the
    ``migrations_current`` health check with it.

    Doubling the escapes is what Alembic's own ini template prescribes.
    ``get_main_option`` — which ``migrations/env.py`` calls — undoubles them, so
    what Alembic dials is byte-for-byte what was passed in.
    """
    return value.replace("%", "%%")


def make_alembic_config(config: CatalogDatabaseConfig) -> Config:
    config.assert_not_reserved_database()
    cfg = Config(str(_INI)) if _INI.exists() else Config()
    cfg.set_main_option("script_location", _ini_literal(str(_SCRIPT_LOCATION)))
    cfg.set_main_option("sqlalchemy.url", _ini_literal(config.url()))
    return cfg


def upgrade(config: CatalogDatabaseConfig, revision: str = "head") -> None:
    command.upgrade(make_alembic_config(config), revision)


def downgrade(config: CatalogDatabaseConfig, revision: str) -> None:
    command.downgrade(make_alembic_config(config), revision)


def stamp(config: CatalogDatabaseConfig, revision: str = "head") -> None:
    command.stamp(make_alembic_config(config), revision)


def script_heads(config: CatalogDatabaseConfig) -> list[str]:
    script = ScriptDirectory.from_config(make_alembic_config(config))
    return list(script.get_heads())


def current_revision(config: CatalogDatabaseConfig) -> str | None:
    config.assert_not_reserved_database()
    engine = create_engine(config.url(), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn, opts={"version_table_schema": SCHEMA_REGISTRY}
            )
            return ctx.get_current_revision()
    finally:
        engine.dispose()
