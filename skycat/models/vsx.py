"""VSX (AAVSO Variable Star Index) data model (``catalog_data`` schema).

VSX has its own typed model — it is *not* forced into the APASS structure. The
columns follow the CDS ``vsx.dat`` byte-by-byte format (OID, Name, variability
flag, J2000 position, type, max/min magnitudes with passbands and flags, epoch,
period, spectral type). Principal magnitudes, period, native id, variable type,
and major flags are typed; rarer per-row flags are preserved in ``extra``.

Release-partitioned like APASS (``LIST (release_id)``).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Double, Index, Integer, Sequence, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..constants import SCHEMA_DATA
from ..database.base import CatalogBase
from .mixins import geom_column

VSX_ID_SEQUENCE = "vsx_source_id_seq"


class VsxSource(CatalogBase):
    """A single VSX variable-star record (release-partitioned)."""

    __tablename__ = "vsx_source"
    # Declared, not created, from here — see the note in ``models/apass.py``.
    # Names must match ``migrations/versions/0003_vsx.py``.
    __table_args__ = (
        Index("ix_vsx_source_geom", "geom", postgresql_using="gist"),
        Index("ix_vsx_source_native_id", "native_id"),
        Index("ix_vsx_source_vsx_oid", "vsx_oid"),
        {
            "schema": SCHEMA_DATA,
            "postgresql_partition_by": "LIST (release_id)",
        },
    )

    release_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence(VSX_ID_SEQUENCE, schema=SCHEMA_DATA),
        primary_key=True,
    )

    # native_id == str(OID); vsx_oid keeps the numeric identifier typed too.
    native_id: Mapped[str] = mapped_column(String(32), nullable=False)
    vsx_oid: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str | None] = mapped_column(String(64))

    ra_deg: Mapped[float] = mapped_column(Double, nullable=False)
    dec_deg: Mapped[float] = mapped_column(Double, nullable=False)

    # 0=Variable, 1=Suspected, 2=Constant/non-existing, 3=Possible duplicate.
    var_flag: Mapped[int | None] = mapped_column(SmallInteger)
    var_type: Mapped[str | None] = mapped_column(String(64))

    max_mag: Mapped[float | None] = mapped_column(Double)
    max_passband: Mapped[str | None] = mapped_column(String(16))
    min_mag: Mapped[float | None] = mapped_column(Double)
    min_passband: Mapped[str | None] = mapped_column(String(16))
    # When ``min_is_amplitude`` is true the "min" column is actually an amplitude.
    min_is_amplitude: Mapped[bool | None] = mapped_column()
    amplitude_mag: Mapped[float | None] = mapped_column(Double)

    period_days: Mapped[float | None] = mapped_column(Double)
    epoch_hjd: Mapped[float | None] = mapped_column(Double)
    spectral_type: Mapped[str | None] = mapped_column(String(32))

    # Limit/uncertainty flags (l_max, u_max, l_min, u_min, u_Epoch, l/u_Period…).
    extra: Mapped[dict | None] = mapped_column(JSONB)

    geom = geom_column()
