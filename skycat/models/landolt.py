"""Landolt UBVRI standard-star data model (``catalog_data`` schema).

Landolt is modelled as **one family** with two release partitions (1992 and
2009). The stored columns mirror what the source ``table2.dat`` actually carries
— ``V`` plus the five Johnson-Kron-Cousins color indices (``B-V``, ``U-B``,
``V-R``, ``R-I``, ``V-I``) with their mean errors, and the observation/night
counts. The individual ``U``/``B``/``R``/``I`` magnitudes are **not** stored:
the remote VizieR provider derives them from V + the colors at query time, and
the local provider reproduces that exact derivation in normalization, so storing
only what the catalog measured keeps the two backends byte-for-byte consistent.

Per-release quirks (e.g. the source release marker) live in ``extra`` (JSONB);
every queryable field is a typed column. Release-partitioned like APASS/VSX
(``LIST (release_id)``).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Double, Index, Integer, Sequence, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..constants import SCHEMA_DATA
from ..database.base import CatalogBase
from .mixins import geom_column

LANDOLT_ID_SEQUENCE = "landolt_source_id_seq"


class LandoltSource(CatalogBase):
    """A single Landolt standard star (release-partitioned: 1992 / 2009)."""

    __tablename__ = "landolt_source"
    # Declared, not created, from here — see the note in ``models/apass.py``.
    # Names must match ``migrations/versions/0004_landolt.py``.
    __table_args__ = (
        Index("ix_landolt_source_geom", "geom", postgresql_using="gist"),
        Index("ix_landolt_source_native_id", "native_id"),
        {
            "schema": SCHEMA_DATA,
            "postgresql_partition_by": "LIST (release_id)",
        },
    )

    release_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence(LANDOLT_ID_SEQUENCE, schema=SCHEMA_DATA),
        primary_key=True,
    )

    # native_id == the Star/Name designation (e.g. "TPHE A", "92 309"); it carries
    # internal spaces and is the same value the remote provider exposes as ``id``.
    native_id: Mapped[str] = mapped_column(String(32), nullable=False)

    ra_deg: Mapped[float] = mapped_column(Double, nullable=False)
    dec_deg: Mapped[float] = mapped_column(Double, nullable=False)

    # V magnitude + the five color indices (all in mag), with mean errors.
    johnson_v_mag: Mapped[float | None] = mapped_column(Double)
    johnson_v_err_mag: Mapped[float | None] = mapped_column(Double)
    b_minus_v_mag: Mapped[float | None] = mapped_column(Double)
    b_minus_v_err_mag: Mapped[float | None] = mapped_column(Double)
    u_minus_b_mag: Mapped[float | None] = mapped_column(Double)
    u_minus_b_err_mag: Mapped[float | None] = mapped_column(Double)
    v_minus_r_mag: Mapped[float | None] = mapped_column(Double)
    v_minus_r_err_mag: Mapped[float | None] = mapped_column(Double)
    r_minus_i_mag: Mapped[float | None] = mapped_column(Double)
    r_minus_i_err_mag: Mapped[float | None] = mapped_column(Double)
    v_minus_i_mag: Mapped[float | None] = mapped_column(Double)
    v_minus_i_err_mag: Mapped[float | None] = mapped_column(Double)

    # Number of observations (o_Vmag / Nobs) and nights (Nd / Nnig).
    n_obs: Mapped[int | None] = mapped_column(Integer)
    n_nights: Mapped[int | None] = mapped_column(Integer)

    extra: Mapped[dict | None] = mapped_column(JSONB)

    geom = geom_column()
