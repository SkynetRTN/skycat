"""Shared column building blocks for catalog models."""

from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import Computed, DateTime, func
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


# There are deliberately no native_id/ra_deg/dec_deg helpers here. Every family
# declares those explicitly, because they vary: APASS native ids are String(64),
# VSX's are String(32), and index choices differ per family. A one-size helper
# was wrong for half of them and no model ever used it. `geom` is the opposite —
# it must be identical everywhere, so it is the one that gets a helper.
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
