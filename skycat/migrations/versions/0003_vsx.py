"""vsx: release-partitioned VSX data table

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-17
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GEOM_EXPR = (
    "(ST_SetSRID(ST_MakePoint("
    "CASE WHEN ra_deg > 180 THEN ra_deg - 360 ELSE ra_deg END, dec_deg), 4326))::geography"
)


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS catalog_data.vsx_source_id_seq")
    op.execute(
        f"""
        CREATE TABLE catalog_data.vsx_source (
            release_id        integer          NOT NULL,
            id                bigint           NOT NULL DEFAULT nextval('catalog_data.vsx_source_id_seq'),
            native_id         varchar(32)      NOT NULL,
            vsx_oid           bigint,
            name              varchar(64),
            ra_deg            double precision NOT NULL,
            dec_deg           double precision NOT NULL,
            var_flag          smallint,
            var_type          varchar(64),
            max_mag           double precision,
            max_passband      varchar(16),
            min_mag           double precision,
            min_passband      varchar(16),
            min_is_amplitude  boolean,
            amplitude_mag     double precision,
            period_days       double precision,
            epoch_hjd         double precision,
            spectral_type     varchar(32),
            extra             jsonb,
            geom geography(Point,4326) GENERATED ALWAYS AS ({GEOM_EXPR}) STORED,
            PRIMARY KEY (release_id, id)
        ) PARTITION BY LIST (release_id)
        """
    )
    op.execute("ALTER SEQUENCE catalog_data.vsx_source_id_seq OWNED BY catalog_data.vsx_source.id")
    op.execute("CREATE INDEX ix_vsx_source_geom ON catalog_data.vsx_source USING gist (geom)")
    op.execute("CREATE INDEX ix_vsx_source_native_id ON catalog_data.vsx_source (native_id)")
    op.execute("CREATE INDEX ix_vsx_source_vsx_oid ON catalog_data.vsx_source (vsx_oid)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS catalog_data.vsx_source")
    op.execute("DROP SEQUENCE IF EXISTS catalog_data.vsx_source_id_seq")
