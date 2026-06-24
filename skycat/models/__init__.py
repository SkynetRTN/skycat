"""Catalog ORM models — registered on :data:`catalog_metadata` only.

Importing this module imports every model so they are attached to
``CatalogBase.metadata`` (used by Alembic and by the partition helpers).
"""

from .apass import APASS_ID_SEQUENCE, ApassSource
from .registry import (
    CatalogFamily,
    CatalogRelease,
    IngestionRun,
    SourceFile,
    SourceManifest,
    ValidationSummary,
)
from .vsx import VSX_ID_SEQUENCE, VsxSource

__all__ = [
    "APASS_ID_SEQUENCE",
    "ApassSource",
    "CatalogFamily",
    "CatalogRelease",
    "IngestionRun",
    "SourceFile",
    "SourceManifest",
    "ValidationSummary",
    "VSX_ID_SEQUENCE",
    "VsxSource",
]
