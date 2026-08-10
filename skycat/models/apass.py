"""APASS catalog data model (``catalog_data`` schema).

APASS is modelled as **one family** with release-aware partitioning: a single
``LIST (release_id)`` partitioned parent table, one partition per release (DR6,
DR10, …). DR6 and DR10 share this canonical superset schema — bands absent from
a release are simply NULL (e.g. DR6 has no u/z/Y), which preserves the
distinction between "missing" and a real measured value.

Per-release detail that does not warrant a typed column (per-band observation
counts, DR6 B-V, mobs, …) is preserved in the ``extra`` JSONB column. Principal
magnitudes, errors, coordinates and the native id are always typed columns.

``release_id`` deliberately has **no** FK to the registry: attaching a
hundred-million-row partition would otherwise trigger a full FK-validation scan.
The registry remains the source of truth and ingestion only ever writes a valid
release id.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Double, Index, Integer, Sequence, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..constants import SCHEMA_DATA
from ..database.base import CatalogBase
from .mixins import geom_column

APASS_ID_SEQUENCE = "apass_source_id_seq"


class ApassSource(CatalogBase):
    """A single APASS source row (release-partitioned)."""

    __tablename__ = "apass_source"
    # The parent's indexes, declared here because the metadata has to describe
    # the migrated schema — an index that lives only in a migration is one that
    # `alembic revision --autogenerate` proposes dropping. They are *created* by
    # `migrations/versions/0002_apass.py`, not from here (the parent is raw DDL:
    # Alembic's table builder cannot express PARTITION BY or a GENERATED
    # geography column), so the names must match that migration exactly.
    # Indexes on the parent are what every partition gets: the runner replicates
    # each non-PK parent index onto the detached build before ATTACH.
    __table_args__ = (
        Index("ix_apass_source_geom", "geom", postgresql_using="gist"),
        Index("ix_apass_source_native_id", "native_id"),
        {
            "schema": SCHEMA_DATA,
            "postgresql_partition_by": "LIST (release_id)",
        },
    )

    # Composite PK includes the partition key (PostgreSQL requirement).
    release_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence(APASS_ID_SEQUENCE, schema=SCHEMA_DATA),
        primary_key=True,
    )

    native_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ra_deg: Mapped[float] = mapped_column(Double, nullable=False)
    dec_deg: Mapped[float] = mapped_column(Double, nullable=False)
    ra_err_arcsec: Mapped[float | None] = mapped_column(Double)
    dec_err_arcsec: Mapped[float | None] = mapped_column(Double)

    n_obs_total: Mapped[int | None] = mapped_column(Integer)

    johnson_b_mag: Mapped[float | None] = mapped_column(Double)
    johnson_b_err_mag: Mapped[float | None] = mapped_column(Double)
    johnson_v_mag: Mapped[float | None] = mapped_column(Double)
    johnson_v_err_mag: Mapped[float | None] = mapped_column(Double)
    sloan_u_mag: Mapped[float | None] = mapped_column(Double)
    sloan_u_err_mag: Mapped[float | None] = mapped_column(Double)
    sloan_g_mag: Mapped[float | None] = mapped_column(Double)
    sloan_g_err_mag: Mapped[float | None] = mapped_column(Double)
    sloan_r_mag: Mapped[float | None] = mapped_column(Double)
    sloan_r_err_mag: Mapped[float | None] = mapped_column(Double)
    sloan_i_mag: Mapped[float | None] = mapped_column(Double)
    sloan_i_err_mag: Mapped[float | None] = mapped_column(Double)
    sloan_z_mag: Mapped[float | None] = mapped_column(Double)
    sloan_z_err_mag: Mapped[float | None] = mapped_column(Double)
    y_mag: Mapped[float | None] = mapped_column(Double)
    y_err_mag: Mapped[float | None] = mapped_column(Double)

    extra: Mapped[dict | None] = mapped_column(JSONB)

    geom = geom_column()
