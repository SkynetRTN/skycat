"""landolt: release-partitioned Landolt UBVRI standards data table

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-24

One ``LIST (release_id)`` partitioned parent (V + the five color indices with
errors, plus observation/night counts) and its sequence + parent indexes. The
1992 and 2009 releases are separate partitions created by the importer at ingest
time. The geography GENERATED column + GiST index propagate to each partition.
APASS/VSX structures are untouched.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Immutable snapshot of the geom generation expression (matches the other tables).
GEOM_EXPR = (
    "(ST_SetSRID(ST_MakePoint("
    "CASE WHEN ra_deg > 180 THEN ra_deg - 360 ELSE ra_deg END, dec_deg), 4326))::geography"
)


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS catalog_data.landolt_source_id_seq")
    op.execute(
        f"""
        CREATE TABLE catalog_data.landolt_source (
            release_id          integer          NOT NULL,
            id                  bigint           NOT NULL DEFAULT nextval('catalog_data.landolt_source_id_seq'),
            native_id           varchar(32)      NOT NULL,
            ra_deg              double precision NOT NULL,
            dec_deg             double precision NOT NULL,
            johnson_v_mag       double precision,
            johnson_v_err_mag   double precision,
            b_minus_v_mag       double precision,
            b_minus_v_err_mag   double precision,
            u_minus_b_mag       double precision,
            u_minus_b_err_mag   double precision,
            v_minus_r_mag       double precision,
            v_minus_r_err_mag   double precision,
            r_minus_i_mag       double precision,
            r_minus_i_err_mag   double precision,
            v_minus_i_mag       double precision,
            v_minus_i_err_mag   double precision,
            n_obs               integer,
            n_nights            integer,
            extra               jsonb,
            geom geography(Point,4326) GENERATED ALWAYS AS ({GEOM_EXPR}) STORED,
            PRIMARY KEY (release_id, id)
        ) PARTITION BY LIST (release_id)
        """
    )
    op.execute(
        "ALTER SEQUENCE catalog_data.landolt_source_id_seq "
        "OWNED BY catalog_data.landolt_source.id"
    )
    op.execute("CREATE INDEX ix_landolt_source_geom ON catalog_data.landolt_source USING gist (geom)")
    op.execute("CREATE INDEX ix_landolt_source_native_id ON catalog_data.landolt_source (native_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS catalog_data.landolt_source")
    op.execute("DROP SEQUENCE IF EXISTS catalog_data.landolt_source_id_seq")
