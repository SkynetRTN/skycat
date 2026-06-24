"""Configuration for the standalone catalog database.

The catalog database has its **own** configuration namespace
(``SKYCAT_DB_*``) and is deliberately decoupled from ``skynet_db`` and
the shared ``SHARED_DB_*`` / ``SKYNET_<APP>_DB_*`` namespaces — connecting to
the catalog store must never reuse the operational ``sky`` connection settings.

The same ``CatalogDatabaseConfig`` works for:

* Docker-internal connections (``host=skycat-postgres``, ``port=5432``),
* host-to-Compose connections (``host=127.0.0.1``, ``port=5433``),
* remote staging / production / Kubernetes Service endpoints.

Nothing here hard-codes a host or port; the host-tool defaults below are merely
convenient for a developer running CLI commands against the local Compose stack.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from urllib.parse import quote, quote_plus, urlencode

from .constants import (
    CatalogRole,
    EXPECTED_DATABASE_NAME,
    FORBIDDEN_DATABASE_NAMES,
    PRODUCTION_DB_NAME_MARKERS,
)

ENV_PREFIX = "SKYCAT_DB_"


def _env(suffix: str, default: str | None = None) -> str | None:
    return os.environ.get(ENV_PREFIX + suffix, default)


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _opt_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


@dataclass(frozen=True)
class CatalogDatabaseConfig:
    """A single connection identity to the catalog database."""

    backend: str = "postgresql+psycopg"
    host: str = "127.0.0.1"
    port: int = 5433
    name: str = EXPECTED_DATABASE_NAME
    user: str = "catalog_reader"
    password: str = ""
    sslmode: str | None = None

    pool_size: int = 10
    max_overflow: int = 5
    pool_recycle: int = 300
    pool_timeout: int = 30
    pool_pre_ping: bool = True
    echo: bool = False
    statement_timeout_ms: int | None = None

    # ------------------------------------------------------------------ URL ---
    def url(self, *, hide_password: bool = False) -> str:
        """SQLAlchemy URL for this connection identity.

        ``sslmode`` is passed as a libpq query parameter (psycopg honours it).
        ``statement_timeout`` is applied per-connection by the engine, not in
        the URL, so it works uniformly across drivers.
        """
        password = "***" if hide_password else quote(self.password)
        base = "{backend}://{user}:{password}@{host}:{port}/{name}".format(
            backend=self.backend,
            user=quote_plus(self.user),
            password=password,
            host=self.host,
            port=self.port,
            name=self.name,
        )
        params: dict[str, str] = {}
        if self.sslmode:
            params["sslmode"] = self.sslmode
        if params:
            base = f"{base}?{urlencode(params)}"
        return base

    def safe_url(self) -> str:
        """URL with the password redacted — safe to log / print."""
        return self.url(hide_password=True)

    def target_summary(self) -> str:
        return f"{self.user}@{self.host}:{self.port}/{self.name}"

    # ---------------------------------------------------------------- guards ---
    def is_forbidden_database(self) -> bool:
        return self.name.strip().lower() in FORBIDDEN_DATABASE_NAMES

    def looks_production(self) -> bool:
        lowered = f"{self.host} {self.name}".lower()
        return any(marker in lowered for marker in PRODUCTION_DB_NAME_MARKERS)

    def assert_not_primary_database(self) -> None:
        """Refuse to operate against ``sky`` (or other reserved DB names).

        This is the package's hard guarantee that catalog migrations / ingestion
        can never be pointed at the operational database.
        """
        if self.is_forbidden_database():
            raise CatalogConfigError(
                f"Refusing catalog operation against database {self.name!r}: the "
                f"catalog store must use a dedicated database (expected "
                f"{EXPECTED_DATABASE_NAME!r}), never the primary `sky` database."
            )

    def with_database(self, name: str) -> "CatalogDatabaseConfig":
        return replace(self, name=name)

    def with_credentials(self, user: str, password: str) -> "CatalogDatabaseConfig":
        return replace(self, user=user, password=password)


class CatalogConfigError(RuntimeError):
    """Raised for invalid / unsafe catalog configuration."""


@dataclass(frozen=True)
class CatalogSettings:
    """All catalog configuration, including per-role credentials and roots.

    Resolve a connection identity for a given role with :meth:`config_for`.
    """

    # Shared connection parameters (host/port/name/pool/ssl).
    base: CatalogDatabaseConfig

    # Default app identity (used when no explicit role is requested).
    default_user: str
    default_password: str

    # Per-role credentials. Empty string means "fall back to default".
    bootstrap_user: str
    bootstrap_password: str
    admin_user: str
    admin_password: str
    ingest_user: str
    ingest_password: str
    reader_user: str
    reader_password: str

    # Filesystem roots.
    data_root: str
    work_root: str

    # ------------------------------------------------------------------ env ---
    @classmethod
    def from_env(cls) -> "CatalogSettings":
        base = CatalogDatabaseConfig(
            backend=_env("BACKEND", "postgresql+psycopg") or "postgresql+psycopg",
            host=_env("HOST", "127.0.0.1") or "127.0.0.1",
            port=_int(_env("PORT"), 5433),
            name=_env("NAME", EXPECTED_DATABASE_NAME) or EXPECTED_DATABASE_NAME,
            user=_env("USER", "catalog_reader") or "catalog_reader",
            password=_env("PASSWORD", "") or "",
            sslmode=_env("SSLMODE") or None,
            pool_size=_int(_env("POOL_SIZE"), 10),
            max_overflow=_int(_env("MAX_OVERFLOW"), 5),
            pool_recycle=_int(_env("POOL_RECYCLE"), 300),
            pool_timeout=_int(_env("POOL_TIMEOUT"), 30),
            pool_pre_ping=_bool(_env("POOL_PRE_PING"), True),
            echo=_bool(_env("ECHO"), False),
            statement_timeout_ms=_opt_int(_env("STATEMENT_TIMEOUT")),
        )
        default_user = base.user
        default_password = base.password
        return cls(
            base=base,
            default_user=default_user,
            default_password=default_password,
            bootstrap_user=_env("BOOTSTRAP_USER", "") or "",
            bootstrap_password=_env("BOOTSTRAP_PASSWORD", "") or "",
            admin_user=_env("ADMIN_USER", "") or "",
            admin_password=_env("ADMIN_PASSWORD", "") or "",
            ingest_user=_env("INGEST_USER", "") or "",
            ingest_password=_env("INGEST_PASSWORD", "") or "",
            reader_user=_env("READER_USER", "") or "",
            reader_password=_env("READER_PASSWORD", "") or "",
            data_root=os.environ.get("SKYCAT_DATA_ROOT", "/srv/agents/catalogs"),
            work_root=os.environ.get("SKYCAT_WORK_ROOT", "/tmp/skycat-work"),
        )

    # --------------------------------------------------------------- roles ---
    def _creds_for(self, role: CatalogRole) -> tuple[str, str]:
        mapping = {
            CatalogRole.BOOTSTRAP: (self.bootstrap_user, self.bootstrap_password),
            CatalogRole.ADMIN: (self.admin_user, self.admin_password),
            CatalogRole.INGEST: (self.ingest_user, self.ingest_password),
            CatalogRole.READER: (self.reader_user, self.reader_password),
            CatalogRole.DEFAULT: (self.default_user, self.default_password),
        }
        user, password = mapping[role]
        if not user:
            # Fall back to the default identity if a role wasn't given its own
            # credentials (common in single-credential dev setups).
            user, password = self.default_user, self.default_password
        return user, password

    def config_for(
        self, role: CatalogRole = CatalogRole.DEFAULT
    ) -> CatalogDatabaseConfig:
        user, password = self._creds_for(role)
        return self.base.with_credentials(user, password)


def load_settings() -> CatalogSettings:
    """Convenience loader used by the CLI and services."""
    return CatalogSettings.from_env()
