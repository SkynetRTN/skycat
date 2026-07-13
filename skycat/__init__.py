"""Skycat — standalone PostgreSQL/PostGIS store for large local
astronomical reference catalogs (APASS, VSX, …).

Architecturally independent of ``skynet_db`` and the primary ``sky`` database:
its own declarative base/metadata, engine, Alembic environment, schemas, roles,
and configuration namespace (``SKYCAT_DB_*``).
"""

from __future__ import annotations

from .client import (
    DEFAULT_RELEASE_CACHE_TTL_S,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    CatalogReader,
)
from .config import (
    CatalogConfigError,
    CatalogDatabaseConfig,
    CatalogSettings,
    load_settings,
)
from .constants import (
    ALL_SCHEMAS,
    SCHEMA_DATA,
    SCHEMA_REGISTRY,
    SCHEMA_STAGING,
    CatalogReleaseState,
    CatalogRole,
)
from .database.base import CatalogBase, catalog_metadata

__version__ = "0.1.0"

__all__ = [
    "ALL_SCHEMAS",
    "DEFAULT_RELEASE_CACHE_TTL_S",
    "DEFAULT_STATEMENT_TIMEOUT_MS",
    "CatalogBase",
    "CatalogConfigError",
    "CatalogDatabaseConfig",
    "CatalogReader",
    "CatalogReleaseState",
    "CatalogRole",
    "CatalogSettings",
    "SCHEMA_DATA",
    "SCHEMA_REGISTRY",
    "SCHEMA_STAGING",
    "__version__",
    "catalog_metadata",
    "load_settings",
]
