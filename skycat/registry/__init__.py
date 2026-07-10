"""Catalog registry: code-level family/release definitions + DB-backed ops."""

from .catalog_defs import (
    CATALOG_FAMILIES,
    FAMILY_BY_SLUG,
    FamilyDef,
    ReleaseDef,
    get_family_def,
)
from .families import (
    get_family,
    list_families,
    register_family,
    sync_all_families,
    sync_family,
)
from .releases import (
    ReleaseStateError,
    activate_release,
    deactivate_release,
    get_or_create_release,
    list_releases,
    resolve_active_release,
    resolve_release,
    set_state,
)

__all__ = [
    "CATALOG_FAMILIES",
    "FAMILY_BY_SLUG",
    "FamilyDef",
    "ReleaseDef",
    "ReleaseStateError",
    "activate_release",
    "deactivate_release",
    "get_family",
    "get_family_def",
    "get_or_create_release",
    "list_families",
    "list_releases",
    "register_family",
    "resolve_active_release",
    "resolve_release",
    "set_state",
    "sync_all_families",
    "sync_family",
]
