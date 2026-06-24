"""Catalog-family registry operations (DB-backed)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.registry import CatalogFamily
from .catalog_defs import CATALOG_FAMILIES, FamilyDef, get_family_def


def get_family(session: Session, slug: str) -> CatalogFamily | None:
    return session.scalar(select(CatalogFamily).where(CatalogFamily.slug == slug.lower()))


def list_families(session: Session) -> list[CatalogFamily]:
    return list(session.scalars(select(CatalogFamily).order_by(CatalogFamily.slug)))


def sync_family(session: Session, family_def: FamilyDef) -> CatalogFamily:
    """Create or update a family row from its code-level definition."""
    family = get_family(session, family_def.slug)
    if family is None:
        family = CatalogFamily(slug=family_def.slug)
        session.add(family)
    family.display_name = family_def.display_name
    family.provider = family_def.provider
    family.description = family_def.description
    family.reference_url = family_def.reference_url
    family.catalog_type = family_def.catalog_type
    family.coordinate_frame = family_def.coordinate_frame
    family.epoch_jyear = family_def.epoch_jyear
    family.data_table = family_def.data_table
    if family.enabled is None:
        family.enabled = True
    session.flush()
    return family


def sync_all_families(session: Session) -> list[CatalogFamily]:
    """Register/refresh every known family. Idempotent."""
    return [sync_family(session, fd) for fd in CATALOG_FAMILIES]


def register_family(session: Session, slug: str) -> CatalogFamily:
    """Register a single known family by slug."""
    fd = get_family_def(slug)
    if fd is None:
        raise ValueError(f"Unknown catalog family slug: {slug!r}")
    return sync_family(session, fd)
