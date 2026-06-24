"""apass: release-partitioned APASS data table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-17

One ``LIST (release_id)`` partitioned parent (canonical APASS superset schema)
plus its sequence and parent indexes. Per-release partitions are created by the
importer at ingest time (not here, and never with catalog *data* in a migration).
The geography GENERATED column + GiST index propagate to each partition.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# NOTE: kept as an immutable snapshot of the generation expression.
GEOM_EXPR = (
    "(ST_SetSRID(ST_MakePoint("
    "CASE WHEN ra_deg > 180 THEN ra_deg - 360 ELSE ra_deg END, dec_deg), 4326))::geography"
)


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS catalog_data.apass_source_id_seq")
    op.execute(
        f"""
        CREATE TABLE catalog_data.apass_source (
            release_id       integer          NOT NULL,
            id               bigint           NOT NULL DEFAULT nextval('catalog_data.apass_source_id_seq'),
            native_id        varchar(64)      NOT NULL,
            ra_deg           double precision NOT NULL,
            dec_deg          double precision NOT NULL,
            ra_err_arcsec    double precision,
            dec_err_arcsec   double precision,
            n_obs_total      integer,
            johnson_b_mag    double precision,
            johnson_b_err_mag double precision,
            johnson_v_mag    double precision,
            johnson_v_err_mag double precision,
            sloan_u_mag      double precision,
            sloan_u_err_mag  double precision,
            sloan_g_mag      double precision,
            sloan_g_err_mag  double precision,
            sloan_r_mag      double precision,
            sloan_r_err_mag  double precision,
            sloan_i_mag      double precision,
            sloan_i_err_mag  double precision,
            sloan_z_mag      double precision,
            sloan_z_err_mag  double precision,
            y_mag            double precision,
            y_err_mag        double precision,
            extra            jsonb,
            geom geography(Point,4326) GENERATED ALWAYS AS ({GEOM_EXPR}) STORED,
            PRIMARY KEY (release_id, id)
        ) PARTITION BY LIST (release_id)
        """
    )
    op.execute("ALTER SEQUENCE catalog_data.apass_source_id_seq OWNED BY catalog_data.apass_source.id")
    op.execute("CREATE INDEX ix_apass_source_geom ON catalog_data.apass_source USING gist (geom)")
    op.execute("CREATE INDEX ix_apass_source_native_id ON catalog_data.apass_source (native_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS catalog_data.apass_source")
    op.execute("DROP SEQUENCE IF EXISTS catalog_data.apass_source_id_seq")
