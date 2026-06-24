"""APASS-specific staging validation (warnings, not row rejections)."""

from __future__ import annotations

from sqlalchemy import Connection, text

from .common import INFO, WARNING, Check

_MAG_COLS = (
    "johnson_b_mag", "johnson_v_mag", "sloan_u_mag", "sloan_g_mag",
    "sloan_r_mag", "sloan_i_mag", "sloan_z_mag", "y_mag",
)
_ERR_COLS = tuple(c.replace("_mag", "_err_mag") for c in _MAG_COLS)


def validate_apass_staging(conn: Connection, staging_fqn: str) -> list[Check]:
    checks: list[Check] = []

    # Magnitude sanity: any measured mag wildly outside [-5, 40].
    mag_pred = " OR ".join(f"({c} IS NOT NULL AND ({c} < -5 OR {c} > 40))" for c in _MAG_COLS)
    bad_mag = int(conn.execute(text(
        f"SELECT count(*) FROM {staging_fqn} WHERE {mag_pred}"
    )).scalar() or 0)
    checks.append(Check("apass_magnitude_range", WARNING, bad_mag == 0,
                        f"{bad_mag} rows with an out-of-range magnitude"))

    # Negative photometric errors.
    err_pred = " OR ".join(f"({c} IS NOT NULL AND {c} < 0)" for c in _ERR_COLS)
    bad_err = int(conn.execute(text(
        f"SELECT count(*) FROM {staging_fqn} WHERE {err_pred}"
    )).scalar() or 0)
    checks.append(Check("apass_error_validity", WARNING, bad_err == 0,
                        f"{bad_err} rows with a negative magnitude error"))

    # At least one photometric band present.
    none_pred = " AND ".join(f"{c} IS NULL" for c in _MAG_COLS)
    no_phot = int(conn.execute(text(
        f"SELECT count(*) FROM {staging_fqn} WHERE reject_reason IS NULL AND ({none_pred})"
    )).scalar() or 0)
    checks.append(Check("apass_band_mapping", WARNING, no_phot == 0,
                        f"{no_phot} valid rows have no photometric band"))

    # Duplicate native ids (DR6 field-block names legitimately repeat -> info).
    dups = int(conn.execute(text(
        f"SELECT count(*) FROM (SELECT native_id FROM {staging_fqn} "
        f"GROUP BY native_id HAVING count(*) > 1) d"
    )).scalar() or 0)
    checks.append(Check("apass_duplicate_native_id", INFO, True,
                        f"{dups} native_id values appear more than once"))
    return checks
