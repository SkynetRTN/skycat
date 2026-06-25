"""stetson: release-partitioned Stetson globular-cluster UBVRI data table

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-24

One ``LIST (release_id)`` partitioned parent (U/B/V/R/I with errors + per-band
counts, DAOPHOT chi/sharp, Welch-Stetson variability index/weight, cluster name)
and its sequence + parent indexes. The StetsonGlobs release is a partition the
importer creates at ingest time. A native_id index supports per-star lookup and a
``cluster`` index supports field-name filtering. The geography GENERATED column +
GiST index propagate to each partition. APASS/VSX/Landolt structures are
untouched.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GEOM_EXPR = (
    "(ST_SetSRID(ST_MakePoint("
    "CASE WHEN ra_deg > 180 THEN ra_deg - 360 ELSE ra_deg END, dec_deg), 4326))::geography"
)


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS catalog_data.stetson_source_id_seq")
    op.execute(
        f"""
        CREATE TABLE catalog_data.stetson_source (
            release_id          integer          NOT NULL,
            id                  bigint           NOT NULL DEFAULT nextval('catalog_data.stetson_source_id_seq'),
            native_id           varchar(32)      NOT NULL,
            cluster             varchar(16)      NOT NULL,
            ra_deg              double precision NOT NULL,
            dec_deg             double precision NOT NULL,
            johnson_u_mag       double precision,
            johnson_u_err_mag   double precision,
            n_obs_u             integer,
            johnson_b_mag       double precision,
            johnson_b_err_mag   double precision,
            n_obs_b             integer,
            johnson_v_mag       double precision,
            johnson_v_err_mag   double precision,
            n_obs_v             integer,
            cousins_r_mag       double precision,
            cousins_r_err_mag   double precision,
            n_obs_r             integer,
            cousins_i_mag       double precision,
            cousins_i_err_mag   double precision,
            n_obs_i             integer,
            chi                 double precision,
            sharp               double precision,
            variability_index   double precision,
            variability_weight  double precision,
            extra               jsonb,
            geom geography(Point,4326) GENERATED ALWAYS AS ({GEOM_EXPR}) STORED,
            PRIMARY KEY (release_id, id)
        ) PARTITION BY LIST (release_id)
        """
    )
    op.execute(
        "ALTER SEQUENCE catalog_data.stetson_source_id_seq "
        "OWNED BY catalog_data.stetson_source.id"
    )
    op.execute("CREATE INDEX ix_stetson_source_geom ON catalog_data.stetson_source USING gist (geom)")
    op.execute("CREATE INDEX ix_stetson_source_native_id ON catalog_data.stetson_source (native_id)")
    op.execute("CREATE INDEX ix_stetson_source_cluster ON catalog_data.stetson_source (cluster)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS catalog_data.stetson_source")
    op.execute("DROP SEQUENCE IF EXISTS catalog_data.stetson_source_id_seq")
