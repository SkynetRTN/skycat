"""Stetson-specific staging validation.

The common validator handles coordinate ranges and null native ids. Here we
additionally **reject** rows with no cluster (the production table requires it)
and add quality checks: magnitude/error ranges, per-band counts, partial-band
rows, field-name preservation, and identifier scope. The ``Star`` id is unique
only within a cluster, so a duplicate *native_id* across the release is expected
(INFO); a duplicate ``(cluster, native_id)`` pair is not (WARNING).
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from .common import CRITICAL, INFO, WARNING, Check

_MAG_COLS = (
    "johnson_u_mag", "johnson_b_mag", "johnson_v_mag", "cousins_r_mag", "cousins_i_mag",
)
_ERR_COLS = tuple(c.replace("_mag", "_err_mag") for c in _MAG_COLS)


def _count(conn: Connection, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar() or 0)


def validate_stetson_staging(conn: Connection, staging_fqn: str) -> list[Check]:
    checks: list[Check] = []

    # Cluster (field name) is required by the production table — reject empties
    # (the parser already drops these as malformed, so this is a safety net).
    conn.execute(text(
        f"UPDATE {staging_fqn} SET reject_reason = 'null_cluster' "
        f"WHERE reject_reason IS NULL AND (cluster IS NULL OR cluster = '')"
    ))
    null_cluster = _count(conn, f"SELECT count(*) FROM {staging_fqn} WHERE reject_reason = 'null_cluster'")
    checks.append(Check("stetson_cluster_present", CRITICAL, null_cluster == 0,
                        f"{null_cluster} rows with null/empty cluster rejected"))

    # Magnitude sanity (any band wildly outside [-5, 40]).
    mag_pred = " OR ".join(f"({c} IS NOT NULL AND ({c} < -5 OR {c} > 40))" for c in _MAG_COLS)
    bad_mag = _count(conn, f"SELECT count(*) FROM {staging_fqn} WHERE {mag_pred}")
    checks.append(Check("stetson_magnitude_range", WARNING, bad_mag == 0,
                        f"{bad_mag} rows with an out-of-range magnitude"))

    # Negative photometric errors.
    err_pred = " OR ".join(f"({c} IS NOT NULL AND {c} < 0)" for c in _ERR_COLS)
    bad_err = _count(conn, f"SELECT count(*) FROM {staging_fqn} WHERE {err_pred}")
    checks.append(Check("stetson_error_validity", WARNING, bad_err == 0,
                        f"{bad_err} rows with a negative magnitude error"))

    # At least one photometric band present (partial-band rows are allowed; a row
    # with no band at all is unusable — mirror APASS's warning, not a rejection).
    none_pred = " AND ".join(f"{c} IS NULL" for c in _MAG_COLS)
    no_phot = _count(conn, f"SELECT count(*) FROM {staging_fqn} "
                           f"WHERE reject_reason IS NULL AND ({none_pred})")
    checks.append(Check("stetson_band_mapping", WARNING, no_phot == 0,
                        f"{no_phot} valid rows have no photometric band"))

    # Identifier scope: (cluster, native_id) should be unique within the release;
    # native_id alone repeats across clusters (expected -> INFO).
    pair_dups = _count(
        conn,
        f"SELECT count(*) FROM (SELECT cluster, native_id FROM {staging_fqn} "
        f"WHERE reject_reason IS NULL GROUP BY cluster, native_id HAVING count(*) > 1) d",
    )
    checks.append(Check("stetson_id_unique_per_cluster", WARNING, pair_dups == 0,
                        f"{pair_dups} duplicate (cluster, Star) pairs"))

    fields = _count(
        conn,
        f"SELECT count(DISTINCT cluster) FROM {staging_fqn} WHERE reject_reason IS NULL",
    )
    checks.append(Check("stetson_field_count", INFO, fields > 0,
                        f"{fields} distinct clusters (fields)"))
    valid = _count(conn, f"SELECT count(*) FROM {staging_fqn} WHERE reject_reason IS NULL")
    checks.append(Check("stetson_row_count", INFO, valid > 0, f"{valid} valid star rows"))
    return checks
