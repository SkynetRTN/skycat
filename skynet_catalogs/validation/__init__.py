"""Validation: common + per-family checks."""

from __future__ import annotations

from sqlalchemy import Connection

from .apass import validate_apass_staging
from .common import (
    CRITICAL,
    INFO,
    WARNING,
    Check,
    summarize,
    validate_production,
    validate_staging_common,
)
from .vsx import validate_vsx_staging

_FAMILY_VALIDATORS = {
    "apass": validate_apass_staging,
    "vsx": validate_vsx_staging,
}


def validate_family_staging(family_slug: str, conn: Connection, staging_fqn: str) -> list[Check]:
    validator = _FAMILY_VALIDATORS.get(family_slug.lower())
    return validator(conn, staging_fqn) if validator else []


__all__ = [
    "CRITICAL",
    "Check",
    "INFO",
    "WARNING",
    "summarize",
    "validate_apass_staging",
    "validate_family_staging",
    "validate_production",
    "validate_staging_common",
    "validate_vsx_staging",
]
