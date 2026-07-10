"""Landolt-specific staging validation (warnings, not row rejections).

Coordinate ranges and null ids are handled by the common validator; these checks
add Landolt-specific quality signals: V presence (the remote provider derives all
bands from V + colors, so a missing V makes a row unusable), magnitude/color/error
ranges, and native-id (designation) uniqueness within the release.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from .common import CRITICAL, INFO, WARNING, Check

_COLOR_COLS = (
    "b_minus_v_mag", "u_minus_b_mag", "v_minus_r_mag", "r_minus_i_mag", "v_minus_i_mag",
)
_ERR_COLS = (
    "johnson_v_err_mag", "b_minus_v_err_mag", "u_minus_b_err_mag",
    "v_minus_r_err_mag", "r_minus_i_err_mag", "v_minus_i_err_mag",
)


def _count(conn: Connection, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar() or 0)


def validate_landolt_staging(conn: Connection, staging_fqn: str) -> list[Check]:
    checks: list[Check] = []

    # V magnitude present on every non-rejected row (required to derive U/B/R/I).
    no_v = _count(
        conn,
        f"SELECT count(*) FROM {staging_fqn} "
        f"WHERE reject_reason IS NULL AND johnson_v_mag IS NULL",
    )
    checks.append(Check("landolt_v_present", WARNING, no_v == 0,
                        f"{no_v} valid rows missing V magnitude"))

    # V range (Landolt covers ~8.9 < V < 16.3; flag anything wildly outside).
    bad_v = _count(
        conn,
        f"SELECT count(*) FROM {staging_fqn} "
        f"WHERE johnson_v_mag IS NOT NULL AND (johnson_v_mag < 0 OR johnson_v_mag > 25)",
    )
    checks.append(Check("landolt_v_range", WARNING, bad_v == 0,
                        f"{bad_v} rows with V outside [0,25]"))

    # Color-index range (B-V spans ~-0.35..+2.3; allow a generous band).
    color_pred = " OR ".join(
        f"({c} IS NOT NULL AND ({c} < -2 OR {c} > 6))" for c in _COLOR_COLS
    )
    bad_color = _count(conn, f"SELECT count(*) FROM {staging_fqn} WHERE {color_pred}")
    checks.append(Check("landolt_color_range", WARNING, bad_color == 0,
                        f"{bad_color} rows with a color index outside [-2,6]"))

    # Negative photometric errors.
    err_pred = " OR ".join(f"({c} IS NOT NULL AND {c} < 0)" for c in _ERR_COLS)
    bad_err = _count(conn, f"SELECT count(*) FROM {staging_fqn} WHERE {err_pred}")
    checks.append(Check("landolt_error_validity", WARNING, bad_err == 0,
                        f"{bad_err} rows with a negative error"))

    # Designation uniqueness within the release (standard stars are one row each).
    dups = _count(
        conn,
        f"SELECT count(*) FROM (SELECT native_id FROM {staging_fqn} "
        f"WHERE reject_reason IS NULL GROUP BY native_id HAVING count(*) > 1) d",
    )
    checks.append(Check("landolt_native_id_unique", WARNING, dups == 0,
                        f"{dups} duplicate designations within the release"))

    valid = _count(conn, f"SELECT count(*) FROM {staging_fqn} WHERE reject_reason IS NULL")
    checks.append(Check("landolt_row_count", INFO, valid > 0, f"{valid} standard stars"))
    return checks
