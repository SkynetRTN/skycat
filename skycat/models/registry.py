"""Registry / management metadata models (``catalog_registry`` schema).

These describe catalog *families* and their *releases* independently from the
bulk catalog rows, and record ingestion runs, validation, and source manifests.
They never reference any ``skynet_db`` / ``sky`` table.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..constants import (
    SCHEMA_REGISTRY,
    CatalogReleaseState,
    IngestionRunStatus,
    ValidationStatus,
)
from .mixins import TimestampMixin
from ..database.base import CatalogBase


class CatalogFamily(CatalogBase, TimestampMixin):
    """A catalog family (APASS, VSX, Pan-STARRS …) — independent of its data."""

    __tablename__ = "catalog_family"
    __table_args__ = {"schema": SCHEMA_REGISTRY}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    reference_url: Mapped[str | None] = mapped_column(String(512))
    catalog_type: Mapped[str | None] = mapped_column(String(64))
    epoch_jyear: Mapped[float | None] = mapped_column()
    coordinate_frame: Mapped[str | None] = mapped_column(String(32))
    data_table: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    default_release_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA_REGISTRY}.catalog_release.id", use_alter=True, ondelete="SET NULL")
    )

    releases: Mapped[list["CatalogRelease"]] = relationship(
        back_populates="family",
        foreign_keys="CatalogRelease.family_id",
        cascade="all, delete-orphan",
    )


class CatalogRelease(CatalogBase, TimestampMixin):
    """An immutable, versioned import of a catalog family."""

    __tablename__ = "catalog_release"
    __table_args__ = (
        UniqueConstraint("family_id", "name", name="uq_catalog_release_family_name"),
        {"schema": SCHEMA_REGISTRY},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA_REGISTRY}.catalog_family.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str | None] = mapped_column(String(64))
    internal_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Source provenance.
    source_location: Mapped[str | None] = mapped_column(Text)
    source_format: Mapped[str | None] = mapped_column(String(64))
    source_checksum: Mapped[str | None] = mapped_column(String(128))
    source_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Row accounting.
    expected_row_count: Mapped[int | None] = mapped_column(BigInteger)
    parsed_row_count: Mapped[int | None] = mapped_column(BigInteger)
    imported_row_count: Mapped[int | None] = mapped_column(BigInteger)
    rejected_row_count: Mapped[int | None] = mapped_column(BigInteger)

    # Lifecycle.
    state: Mapped[CatalogReleaseState] = mapped_column(
        String(32), default=CatalogReleaseState.REGISTERED.value, nullable=False, index=True
    )
    validation_status: Mapped[ValidationStatus] = mapped_column(
        String(32), default=ValidationStatus.NOT_RUN.value, nullable=False
    )

    # Physical production table (partition) backing this release.
    production_table: Mapped[str | None] = mapped_column(String(256))

    importer_version: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    failure_detail: Mapped[dict | None] = mapped_column(JSONB)

    import_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    import_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    family: Mapped[CatalogFamily] = relationship(
        back_populates="releases", foreign_keys=[family_id]
    )
    ingestion_runs: Mapped[list["IngestionRun"]] = relationship(
        back_populates="release", cascade="all, delete-orphan"
    )

    @property
    def is_active(self) -> bool:
        return str(self.state) == CatalogReleaseState.ACTIVE.value


class IngestionRun(CatalogBase):
    """One attempt to ingest a release. Failures retain diagnostics here."""

    __tablename__ = "ingestion_run"
    __table_args__ = {"schema": SCHEMA_REGISTRY}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    release_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA_REGISTRY}.catalog_release.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[IngestionRunStatus] = mapped_column(
        String(32), default=IngestionRunStatus.RUNNING.value, nullable=False
    )
    stage: Mapped[str | None] = mapped_column(String(64))
    importer_version: Mapped[str | None] = mapped_column(String(32))
    host: Mapped[str | None] = mapped_column(String(256))
    source_path: Mapped[str | None] = mapped_column(Text)

    parsed_row_count: Mapped[int | None] = mapped_column(BigInteger)
    loaded_row_count: Mapped[int | None] = mapped_column(BigInteger)
    rejected_row_count: Mapped[int | None] = mapped_column(BigInteger)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)

    release: Mapped[CatalogRelease] = relationship(back_populates="ingestion_runs")


class ValidationSummary(CatalogBase):
    """The outcome of a validation pass over a release."""

    __tablename__ = "validation_summary"
    __table_args__ = {"schema": SCHEMA_REGISTRY}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    release_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA_REGISTRY}.catalog_release.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA_REGISTRY}.ingestion_run.id", ondelete="SET NULL")
    )
    status: Mapped[ValidationStatus] = mapped_column(String(32), nullable=False)
    # List of {name, level (info|warning|critical), passed (bool), detail}.
    checks: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
