"""VSX-specific staging validation."""

from __future__ import annotations

from sqlalchemy import Connection, text

from .common import CRITICAL, INFO, WARNING, Check


def validate_vsx_staging(conn: Connection, staging_fqn: str) -> list[Check]:
    checks: list[Check] = []

    # Period validity (non-positive periods are suspect).
    bad_period = int(conn.execute(text(
        f"SELECT count(*) FROM {staging_fqn} WHERE period_days IS NOT NULL AND period_days <= 0"
    )).scalar() or 0)
    checks.append(Check("vsx_period_validity", WARNING, bad_period == 0,
                        f"{bad_period} rows with non-positive period"))

    # max should be brighter (numerically <=) than min when both present and min
    # is a real magnitude (not an amplitude).
    bad_minmax = int(conn.execute(text(
        f"SELECT count(*) FROM {staging_fqn} "
        f"WHERE max_mag IS NOT NULL AND min_mag IS NOT NULL "
        f"AND min_is_amplitude IS NOT TRUE AND max_mag > min_mag"
    )).scalar() or 0)
    checks.append(Check("vsx_magnitude_consistency", WARNING, bad_minmax == 0,
                        f"{bad_minmax} rows with max fainter than min"))

    # Native identifier (OID) uniqueness — should be globally unique.
    dups = int(conn.execute(text(
        f"SELECT count(*) FROM (SELECT vsx_oid FROM {staging_fqn} "
        f"WHERE reject_reason IS NULL GROUP BY vsx_oid HAVING count(*) > 1) d"
    )).scalar() or 0)
    checks.append(Check("vsx_oid_unique", CRITICAL, dups == 0,
                        f"{dups} duplicate VSX OIDs"))

    # Variable-type preservation (info: how many typed).
    typed = int(conn.execute(text(
        f"SELECT count(*) FROM {staging_fqn} WHERE var_type IS NOT NULL AND var_type <> ''"
    )).scalar() or 0)
    checks.append(Check("vsx_var_type_present", INFO, True, f"{typed} rows carry a variable type"))
    return checks


FAMILY_VALIDATORS = {
    "apass": "validate_apass_staging",
    "vsx": "validate_vsx_staging",
}
