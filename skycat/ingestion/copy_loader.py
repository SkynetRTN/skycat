"""PostgreSQL-native bulk loading via streaming COPY.

Rows are streamed from a parser straight into an UNLOGGED staging table using
``COPY ... FROM STDIN`` — no per-row ORM inserts, bounded memory. The staging
table mirrors the production data columns (plus ``staging_row_id`` and
``reject_reason``) so validation can mark bad rows in place before they reach
production.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from psycopg.types.json import Jsonb
from sqlalchemy import Connection, Table
from sqlalchemy.dialects.postgresql import dialect as pg_dialect

_PG = pg_dialect()
EXCLUDED_FROM_STAGING = {"release_id", "id", "geom"}


def data_columns(table: Table):
    """Production data columns (everything except release_id / id / geom)."""
    return [c for c in table.columns if c.name not in EXCLUDED_FROM_STAGING]


def data_column_names(table: Table) -> list[str]:
    return [c.name for c in data_columns(table)]


def _column_type_sql(col) -> str:
    return col.type.compile(dialect=_PG)


def create_staging_table(conn: Connection, staging_fqn: str, table: Table) -> list[str]:
    """(Re)create an UNLOGGED staging table mirroring the data columns."""
    cols = data_columns(table)
    col_defs = ",\n            ".join(f'"{c.name}" {_column_type_sql(c)}' for c in cols)
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {staging_fqn}")
    conn.exec_driver_sql(
        f"""
        CREATE UNLOGGED TABLE {staging_fqn} (
            staging_row_id bigint GENERATED ALWAYS AS IDENTITY,
            {col_defs},
            reject_reason text
        )
        """
    )
    return [c.name for c in cols]


def copy_rows(
    conn: Connection,
    staging_fqn: str,
    column_names: list[str],
    rows: Iterable[tuple],
    *,
    progress_every: int = 1_000_000,
    on_progress=None,
) -> int:
    """Stream rows into ``staging_fqn`` via COPY. Returns the loaded count."""
    quoted = ", ".join(f'"{c}"' for c in column_names)
    sql = f"COPY {staging_fqn} ({quoted}) FROM STDIN"
    driver = conn.connection.driver_connection  # psycopg3 Connection
    count = 0
    with driver.cursor() as cur:
        with cur.copy(sql) as cp:
            for row in _adapt(rows):
                cp.write_row(row)
                count += 1
                if on_progress and count % progress_every == 0:
                    on_progress(count)
    return count


def _adapt(rows: Iterable[tuple]) -> Iterator[tuple]:
    """Wrap dict values as JSONB so COPY can encode the ``extra`` column."""
    for row in rows:
        if any(isinstance(v, dict) for v in row):
            row = tuple(Jsonb(v) if isinstance(v, dict) else v for v in row)
        yield row
