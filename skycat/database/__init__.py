"""Catalog database layer: separate base/metadata, engine, init, roles, PostGIS."""

from .base import CatalogBase, catalog_metadata
from .engine import (
    create_catalog_engine,
    create_session_factory,
    fetch_current_database,
    session_scope,
)

__all__ = [
    "CatalogBase",
    "catalog_metadata",
    "create_catalog_engine",
    "create_session_factory",
    "fetch_current_database",
    "session_scope",
]
