"""Catalog ORM models — registered on :data:`catalog_metadata` only.

Importing this module imports every model so they are attached to
``CatalogBase.metadata`` (used by Alembic and by the partition helpers).
"""

from .apass import APASS_ID_SEQUENCE, ApassSource
from .landolt import LANDOLT_ID_SEQUENCE, LandoltSource
from .registry import (
    CatalogFamily,
    CatalogRelease,
    IngestionRun,
    SourceFile,
    SourceManifest,
    ValidationSummary,
)
from .stetson import STETSON_ID_SEQUENCE, StetsonSource
from .vsx import VSX_ID_SEQUENCE, VsxSource

__all__ = [
    "APASS_ID_SEQUENCE",
    "ApassSource",
    "CatalogFamily",
    "CatalogRelease",
    "IngestionRun",
    "LANDOLT_ID_SEQUENCE",
    "LandoltSource",
    "SourceFile",
    "SourceManifest",
    "STETSON_ID_SEQUENCE",
    "StetsonSource",
    "ValidationSummary",
    "VSX_ID_SEQUENCE",
    "VsxSource",
]
