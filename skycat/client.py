"""``CatalogReader`` — the read path consumers should use.

Every consumer (the optical pipeline, the public-api ``/catalogs`` router, future
workers) otherwise rewrites the same three lines: load settings, build a reader
engine, resolve the active release on every call. This module does it once, and
adds the two behaviours a shared read path needs:

* **Active-release caching.** Resolving the active release is a registry query
  on the hot path of every cone search. Activation is a rare, deliberate
  operation, so the result is cached for ``release_cache_ttl_s`` (60s default)
  and a query becomes a single indexed round trip. A newly activated release is
  picked up within the TTL; :meth:`CatalogReader.invalidate` forces it sooner.
* **A default statement timeout.** ``SKYCAT_DB_STATEMENT_TIMEOUT`` exists but
  nothing sets it, so one pathological query can wedge a single-threaded worker
  indefinitely. Reader connections get a bounded timeout unless configured
  otherwise.

Sync by design: FastAPI runs sync dependencies in its threadpool and the
pipeline is sync. The instance is thread-safe and meant to be held at app /
worker scope — one pooled engine, shared.

    reader = CatalogReader.from_env()
    stars = reader.cone("apass", ra, dec, radius_deg=0.2,
                        order_by="johnson_v_mag", limit=500)
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from .config import CatalogRole, CatalogSettings, load_settings
from .database.engine import create_catalog_engine
from .query import (
    QualityFilter,
    ResolvedRelease,
    batch_crossmatch,
    cone_search,
    lookup_native_id,
    radius_to_deg,
    resolve_release_for_query,
)

__all__ = [
    "DEFAULT_RELEASE_CACHE_TTL_S",
    "DEFAULT_STATEMENT_TIMEOUT_MS",
    "CatalogReader",
]

#: Bounded by default so a pathological query cannot wedge a worker forever.
DEFAULT_STATEMENT_TIMEOUT_MS = 30_000

#: Activation is rare; a short TTL keeps the hot path to one indexed query.
DEFAULT_RELEASE_CACHE_TTL_S = 60.0


@dataclass(frozen=True)
class _CachedRelease:
    resolved: ResolvedRelease
    expires_at: float


class CatalogReader:
    """Pooled, read-only entry point to the catalog store.

    Holds one engine bound to the ``catalog_reader`` role. Consumers speak
    (family, ra, dec, radius, band, order, limit) — no SQLAlchemy or PostGIS
    detail crosses this boundary.
    """

    def __init__(
        self,
        settings: CatalogSettings,
        *,
        statement_timeout_ms: int | None = DEFAULT_STATEMENT_TIMEOUT_MS,
        release_cache_ttl_s: float = DEFAULT_RELEASE_CACHE_TTL_S,
        pool_size: int | None = None,
        max_overflow: int | None = None,
    ) -> None:
        self._settings = settings
        self._statement_timeout_ms = statement_timeout_ms
        self._release_cache_ttl_s = release_cache_ttl_s
        self._pool_size = pool_size
        self._max_overflow = max_overflow

        self._engine: Engine | None = None
        self._engine_lock = threading.Lock()
        self._cache: dict[str, _CachedRelease] = {}
        self._cache_lock = threading.Lock()

    @classmethod
    def from_env(cls, **kwargs) -> "CatalogReader":
        """Build a reader from the ``SKYCAT_DB_*`` environment."""
        return cls(load_settings(), **kwargs)

    # ---------------------------------------------------------------- engine --
    @property
    def engine(self) -> Engine:
        """The pooled reader engine, created on first use.

        Lazy so that constructing a reader at app scope (import time, a FastAPI
        dependency) never requires the database to be up.
        """
        if self._engine is None:
            with self._engine_lock:
                if self._engine is None:  # re-check under the lock
                    self._engine = self._create_engine()
        return self._engine

    def _create_engine(self) -> Engine:
        cfg = self._settings.config_for(CatalogRole.READER)
        cfg.assert_not_primary_database()
        # An explicitly configured timeout always wins; the default only fills
        # the gap when nothing is set.
        if cfg.statement_timeout_ms is None and self._statement_timeout_ms:
            cfg = replace(cfg, statement_timeout_ms=self._statement_timeout_ms)
        overrides = {}
        if self._pool_size is not None:
            overrides["pool_size"] = self._pool_size
        if self._max_overflow is not None:
            overrides["max_overflow"] = self._max_overflow
        return create_catalog_engine(cfg, **overrides)

    # --------------------------------------------------------------- releases --
    def active_release(self, family: str, *, refresh: bool = False) -> ResolvedRelease:
        """Resolve a family's active release, served from the TTL cache."""
        if not refresh:
            with self._cache_lock:
                hit = self._cache.get(family)
            if hit is not None and hit.expires_at > time.monotonic():
                return hit.resolved

        # Resolved outside the lock: a concurrent miss costs a duplicate lookup,
        # which is cheaper than serializing every query behind one mutex.
        with Session(self.engine) as session:
            resolved = resolve_release_for_query(session, family, None)
        with self._cache_lock:
            self._cache[family] = _CachedRelease(
                resolved=resolved,
                expires_at=time.monotonic() + self._release_cache_ttl_s,
            )
        return resolved

    def invalidate(self, family: str | None = None) -> None:
        """Drop cached release(s) — call after an activation to pick it up now."""
        with self._cache_lock:
            if family is None:
                self._cache.clear()
            else:
                self._cache.pop(family, None)

    def _resolve(self, family: str, release: str | None) -> ResolvedRelease:
        """Explicit releases bypass the cache — they are an ops/parity path."""
        if release is not None:
            with Session(self.engine) as session:
                return resolve_release_for_query(session, family, release)
        return self.active_release(family)

    # ---------------------------------------------------------------- queries --
    def cone(
        self,
        family: str,
        ra_deg: float,
        dec_deg: float,
        *,
        radius_deg: float | None = None,
        radius_arcmin: float | None = None,
        radius_arcsec: float | None = None,
        limit: int = 100,
        mag_band: str | None = None,
        mag_min: float | None = None,
        mag_max: float | None = None,
        order_by: str | None = None,
        quality_filter: Sequence[QualityFilter] | None = None,
        release: str | None = None,
    ) -> list[dict]:
        """Cone search. ``order_by="<band>_mag"`` selects brightest-first."""
        rdeg = radius_to_deg(
            radius_deg=radius_deg,
            radius_arcmin=radius_arcmin,
            radius_arcsec=radius_arcsec,
        )
        return cone_search(
            self._settings,
            family,
            ra_deg,
            dec_deg,
            radius_deg=rdeg,
            limit=limit,
            mag_band=mag_band,
            mag_min=mag_min,
            mag_max=mag_max,
            order_by=order_by,
            quality_filter=quality_filter,
            engine=self.engine,
            resolved=self._resolve(family, release),
        )

    def crossmatch(
        self,
        family: str,
        inputs: Iterable[tuple[str, float, float]],
        *,
        radius_deg: float | None = None,
        radius_arcmin: float | None = None,
        radius_arcsec: float | None = None,
        nearest_only: bool = True,
        max_candidates: int = 1,
        release: str | None = None,
    ) -> list[dict]:
        """Batch crossmatch ``(input_id, ra_deg, dec_deg)`` tuples."""
        rdeg = radius_to_deg(
            radius_deg=radius_deg,
            radius_arcmin=radius_arcmin,
            radius_arcsec=radius_arcsec,
        )
        return batch_crossmatch(
            self._settings,
            family,
            inputs,
            radius_deg=rdeg,
            nearest_only=nearest_only,
            max_candidates=max_candidates,
            engine=self.engine,
            resolved=self._resolve(family, release),
        )

    def lookup(
        self,
        family: str,
        native_id: str,
        *,
        limit: int = 100,
        release: str | None = None,
    ) -> list[dict]:
        """Look a source up by its catalog-native identifier."""
        return lookup_native_id(
            self._settings,
            family,
            native_id,
            limit=limit,
            engine=self.engine,
            resolved=self._resolve(family, release),
        )

    # ------------------------------------------------------------------- life --
    def close(self) -> None:
        """Dispose the pooled engine and drop the cache."""
        with self._engine_lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None
        self.invalidate()

    def __enter__(self) -> "CatalogReader":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
