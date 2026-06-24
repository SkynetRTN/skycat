"""Catalog-release lifecycle operations (DB-backed).

Activation is atomic and guarded so that:
* a release only activates from READY (or is re-affirmed if already ACTIVE);
* at most one release per family is ACTIVE (also enforced by a partial unique
  index in the schema);
* the previously active release is SUPERSEDED, never simply deleted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import CatalogReleaseState
from ..models.registry import CatalogFamily, CatalogRelease


class ReleaseStateError(RuntimeError):
    """Illegal release-state transition."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_release(
    session: Session,
    family: CatalogFamily,
    *,
    name: str,
    version: str | None = None,
    internal_schema_version: int = 1,
    importer_version: str | None = None,
) -> CatalogRelease:
    release = session.scalar(
        select(CatalogRelease).where(
            CatalogRelease.family_id == family.id, CatalogRelease.name == name
        )
    )
    if release is None:
        release = CatalogRelease(
            family_id=family.id,
            name=name,
            version=version,
            internal_schema_version=internal_schema_version,
            importer_version=importer_version,
            state=CatalogReleaseState.REGISTERED.value,
        )
        session.add(release)
        session.flush()
    return release


def resolve_release(
    session: Session, family_slug: str, release_ref: str
) -> CatalogRelease | None:
    """Resolve a release by family slug + (release slug or registry name)."""
    family = session.scalar(select(CatalogFamily).where(CatalogFamily.slug == family_slug.lower()))
    if family is None:
        return None
    ref = release_ref.lower()
    for rel in session.scalars(
        select(CatalogRelease).where(CatalogRelease.family_id == family.id)
    ):
        if rel.name.lower() == ref or (rel.version or "").lower() == ref:
            return rel
    return None


def resolve_active_release(session: Session, family_slug: str) -> CatalogRelease | None:
    family = session.scalar(select(CatalogFamily).where(CatalogFamily.slug == family_slug.lower()))
    if family is None:
        return None
    return session.scalar(
        select(CatalogRelease).where(
            CatalogRelease.family_id == family.id,
            CatalogRelease.state == CatalogReleaseState.ACTIVE.value,
        )
    )


def list_releases(session: Session, family_slug: str | None = None) -> list[CatalogRelease]:
    # Explicit onclause: release<->family have two FKs (family_id and
    # default_release_id), so the join is otherwise ambiguous.
    stmt = select(CatalogRelease).join(
        CatalogFamily, CatalogRelease.family_id == CatalogFamily.id
    )
    if family_slug:
        stmt = stmt.where(CatalogFamily.slug == family_slug.lower())
    return list(session.scalars(stmt.order_by(CatalogFamily.slug, CatalogRelease.name)))


def set_state(session: Session, release: CatalogRelease, state: CatalogReleaseState) -> None:
    release.state = state.value
    session.flush()


def activate_release(session: Session, release: CatalogRelease) -> CatalogRelease:
    """Atomically make ``release`` the single active release for its family."""
    if release.state == CatalogReleaseState.ACTIVE.value:
        return release
    # READY = freshly imported; SUPERSEDED = a previously-active release being
    # rolled back to. Both are fully built (validated + indexed) with a
    # production partition, so both may be (re)activated. A FAILED/incomplete
    # release can never become active.
    _activatable = (CatalogReleaseState.READY.value, CatalogReleaseState.SUPERSEDED.value)
    if release.state not in _activatable:
        raise ReleaseStateError(
            f"Release {release.name!r} is {release.state!r}; only a READY or "
            f"SUPERSEDED release can be activated (it must be fully imported, "
            f"indexed and validated)."
        )

    # Supersede the current active release first so the partial unique index is
    # never momentarily violated.
    current = session.scalar(
        select(CatalogRelease).where(
            CatalogRelease.family_id == release.family_id,
            CatalogRelease.state == CatalogReleaseState.ACTIVE.value,
        )
    )
    if current is not None and current.id != release.id:
        current.state = CatalogReleaseState.SUPERSEDED.value
        current.superseded_at = _now()
        session.flush()

    release.state = CatalogReleaseState.ACTIVE.value
    release.activated_at = _now()
    family = session.get(CatalogFamily, release.family_id)
    if family is not None:
        family.default_release_id = release.id
    session.flush()
    return release


def deactivate_release(session: Session, release: CatalogRelease) -> CatalogRelease:
    if release.state == CatalogReleaseState.ACTIVE.value:
        release.state = CatalogReleaseState.SUPERSEDED.value
        release.superseded_at = _now()
        family = session.get(CatalogFamily, release.family_id)
        if family is not None and family.default_release_id == release.id:
            family.default_release_id = None
        session.flush()
    return release
