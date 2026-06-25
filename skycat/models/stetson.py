"""Stetson globular-cluster UBVRI standards data model (``catalog_data`` schema).

The StetsonGlobs release stores the per-star rows of ``table4.dat``: the five
Johnson-Cousins bands (U/B/V/R/I) with errors and per-band measurement counts,
the DAOPHOT quality parameters (chi/sharp) and the Welch-Stetson variability
index/weight, plus the cluster (field) name and J2000 position.

Identifier semantics match the remote VizieR provider: the ``Star`` id is unique
only **within** a cluster, so ``native_id`` (= ``str(Star)``) repeats across
clusters; the ``cluster`` column carries the field name and is indexed for
field-name filtering. Release-partitioned (``LIST (release_id)``) like the other
families even though there is a single release today.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Double, Integer, Sequence, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..constants import SCHEMA_DATA
from ..database.base import CatalogBase
from .mixins import geom_column

STETSON_ID_SEQUENCE = "stetson_source_id_seq"


class StetsonSource(CatalogBase):
    """A single Stetson globular-cluster standard star (release-partitioned)."""

    __tablename__ = "stetson_source"
    __table_args__ = {
        "schema": SCHEMA_DATA,
        "postgresql_partition_by": "LIST (release_id)",
    }

    release_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence(STETSON_ID_SEQUENCE, schema=SCHEMA_DATA),
        primary_key=True,
    )

    # native_id == str(Star) — unique only within a cluster (matches remote ``id``).
    native_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # Globular-cluster / field name (e.g. "NGC6205"); indexed for field filtering.
    cluster: Mapped[str] = mapped_column(String(16), nullable=False)

    ra_deg: Mapped[float] = mapped_column(Double, nullable=False)
    dec_deg: Mapped[float] = mapped_column(Double, nullable=False)

    johnson_u_mag: Mapped[float | None] = mapped_column(Double)
    johnson_u_err_mag: Mapped[float | None] = mapped_column(Double)
    n_obs_u: Mapped[int | None] = mapped_column(Integer)
    johnson_b_mag: Mapped[float | None] = mapped_column(Double)
    johnson_b_err_mag: Mapped[float | None] = mapped_column(Double)
    n_obs_b: Mapped[int | None] = mapped_column(Integer)
    johnson_v_mag: Mapped[float | None] = mapped_column(Double)
    johnson_v_err_mag: Mapped[float | None] = mapped_column(Double)
    n_obs_v: Mapped[int | None] = mapped_column(Integer)
    cousins_r_mag: Mapped[float | None] = mapped_column(Double)
    cousins_r_err_mag: Mapped[float | None] = mapped_column(Double)
    n_obs_r: Mapped[int | None] = mapped_column(Integer)
    cousins_i_mag: Mapped[float | None] = mapped_column(Double)
    cousins_i_err_mag: Mapped[float | None] = mapped_column(Double)
    n_obs_i: Mapped[int | None] = mapped_column(Integer)

    # DAOPHOT chi/sharp and Welch-Stetson variability index + weight (dimensionless).
    chi: Mapped[float | None] = mapped_column(Double)
    sharp: Mapped[float | None] = mapped_column(Double)
    variability_index: Mapped[float | None] = mapped_column(Double)
    variability_weight: Mapped[float | None] = mapped_column(Double)

    # Cluster-frame pixel/arcsec positions (Xpos/Ypos) preserved here, not queried.
    extra: Mapped[dict | None] = mapped_column(JSONB)

    geom = geom_column()
