"""Shared column building blocks for catalog models."""

from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import Computed, DateTime, Double, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..constants import SRID
from ..spatial import GEOM_GENERATED_EXPR


class TimestampMixin:
    """``created_at`` / ``updated_at`` with server-side defaults."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def native_id_column() -> Mapped[str]:
    """Native source identifier (kept as text; not necessarily unique per row —
    e.g. APASS DR6 field-block names repeat)."""
    return mapped_column(String(64), nullable=False, index=True)


def ra_deg_column() -> Mapped[float]:
    return mapped_column("ra_deg", Double, nullable=False)


def dec_deg_column() -> Mapped[float]:
    return mapped_column("dec_deg", Double, nullable=False)


def geom_column() -> Mapped[WKBElement | None]:
    """The derived ``geography(Point,4326)`` GENERATED column.

    The GiST index is created explicitly in the migrations (per partition), not
    via GeoAlchemy2's auto-index, so ``spatial_index=False`` here.
    """
    return mapped_column(
        Geography(geometry_type="POINT", srid=SRID, spatial_index=False),
        Computed(GEOM_GENERATED_EXPR, persisted=True),
        nullable=True,
    )
