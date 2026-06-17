"""Catalog-independent validation: coordinate ranges, null ids, counts, spatial
index presence. Catalog-specific checks live in sibling modules.

Validation marks bad staging rows (``reject_reason``) rather than dropping them,
so counts and reasons are always recorded. Critical failures block activation;
warnings allow it only with an explicit override.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, text

from ..constants import ValidationStatus

CRITICAL = "critical"
WARNING = "warning"
INFO = "info"


@dataclass
class Check:
    name: str
    level: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "level": self.level, "passed": self.passed, "detail": self.detail}


def summarize(checks: list[Check]) -> ValidationStatus:
    if any((not c.passed) and c.level == CRITICAL for c in checks):
        return ValidationStatus.FAILED
    if any((not c.passed) and c.level == WARNING for c in checks):
        return ValidationStatus.PASSED_WITH_WARNINGS
    return ValidationStatus.PASSED


def _count(conn: Connection, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar() or 0)


def validate_staging_common(conn: Connection, staging_fqn: str) -> tuple[list[Check], int, int]:
    """Mark invalid staging rows and return (checks, valid_count, rejected_count)."""
    checks: list[Check] = []

    # Null / empty native_id (critical).
    conn.execute(text(
        f"UPDATE {staging_fqn} SET reject_reason = 'null_native_id' "
        f"WHERE reject_reason IS NULL AND (native_id IS NULL OR native_id = '')"
    ))
    null_ids = _count(conn, f"SELECT count(*) FROM {staging_fqn} WHERE reject_reason = 'null_native_id'")
    checks.append(Check("native_id_not_null", CRITICAL, null_ids == 0,
                        f"{null_ids} rows with null/empty native_id"))

    # RA range (critical).
    conn.execute(text(
        f"UPDATE {staging_fqn} SET reject_reason = 'ra_out_of_range' "
        f"WHERE reject_reason IS NULL AND (ra_deg IS NULL OR ra_deg < 0 OR ra_deg >= 360)"
    ))
    bad_ra = _count(conn, f"SELECT count(*) FROM {staging_fqn} WHERE reject_reason = 'ra_out_of_range'")
    checks.append(Check("ra_range", CRITICAL, True,
                        f"{bad_ra} rows with RA outside [0,360) rejected"))

    # Dec range (critical).
    conn.execute(text(
        f"UPDATE {staging_fqn} SET reject_reason = 'dec_out_of_range' "
        f"WHERE reject_reason IS NULL AND (dec_deg IS NULL OR dec_deg < -90 OR dec_deg > 90)"
    ))
    bad_dec = _count(conn, f"SELECT count(*) FROM {staging_fqn} WHERE reject_reason = 'dec_out_of_range'")
    checks.append(Check("dec_range", CRITICAL, True,
                        f"{bad_dec} rows with Dec outside [-90,90] rejected"))

    valid = _count(conn, f"SELECT count(*) FROM {staging_fqn} WHERE reject_reason IS NULL")
    rejected = _count(conn, f"SELECT count(*) FROM {staging_fqn} WHERE reject_reason IS NOT NULL")
    checks.append(Check("loaded_row_count", INFO, valid > 0,
                        f"{valid} valid rows, {rejected} rejected"))
    return checks, valid, rejected


def validate_production(conn: Connection, schema: str, table: str) -> list[Check]:
    """Post-load production checks: rows present, spatial index present."""
    checks: list[Check] = []
    rows = _count(conn, f'SELECT count(*) FROM "{schema}"."{table}"')
    checks.append(Check("production_rows", CRITICAL, rows > 0, f"{rows} rows in {schema}.{table}"))

    has_gist = conn.execute(text(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = :s AND tablename = :t "
        "AND indexdef ILIKE '%using gist%'"
    ), {"s": schema, "t": table}).scalar()
    checks.append(Check("spatial_index_present", CRITICAL, bool(has_gist),
                        "GiST spatial index present" if has_gist else "missing GiST index"))
    return checks
