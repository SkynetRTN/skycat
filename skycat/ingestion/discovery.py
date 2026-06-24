"""Source discovery under ``SKYCAT_DATA_ROOT``.

Locates catalog families/releases, verifies their data files exist, computes a
fast *manifest* checksum (filenames + sizes + mtimes — not a multi-GB content
hash unless explicitly requested), and reports missing / ambiguous layouts. It
refuses to guess: an explicit source path can always be supplied. Works
identically on the host (``/srv/agents/catalogs``) and inside Compose
(``/catalog-data``) because the root is configuration.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..registry.catalog_defs import (
    CATALOG_FAMILIES,
    FamilyDef,
    ReleaseDef,
    get_family_def,
)


@dataclass
class DiscoveredFile:
    path: Path
    relative_path: str
    role: str
    size_bytes: int
    modified_at: datetime


@dataclass
class DiscoveredRelease:
    family: FamilyDef
    release: ReleaseDef
    source_dir: Path
    data_files: list[DiscoveredFile] = field(default_factory=list)
    aux_files: list[DiscoveredFile] = field(default_factory=list)
    present: bool = False
    issues: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.data_files)

    @property
    def file_count(self) -> int:
        return len(self.data_files)

    def manifest_checksum(self) -> str:
        return compute_manifest_checksum(self.data_files)

    def latest_modified(self) -> datetime | None:
        if not self.data_files:
            return None
        return max(f.modified_at for f in self.data_files)


def compute_manifest_checksum(files: list[DiscoveredFile]) -> str:
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: x.relative_path):
        h.update(
            f"{f.relative_path}:{f.size_bytes}:{int(f.modified_at.timestamp())}\n".encode()
        )
    return h.hexdigest()


def compute_content_checksum(
    files: list[DiscoveredFile], *, chunk: int = 1 << 20
) -> str:
    """Full content hash (streamed). Expensive on multi-GB sets — opt-in."""
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: x.relative_path):
        h.update(f.relative_path.encode())
        with open(f.path, "rb") as fh:
            while data := fh.read(chunk):
                h.update(data)
    return h.hexdigest()


def _scan(
    source_dir: Path, patterns: tuple[str, ...], role: str
) -> list[DiscoveredFile]:
    found: dict[str, DiscoveredFile] = {}
    for pattern in patterns:
        for path in sorted(source_dir.glob(pattern)):
            if not path.is_file():
                continue
            stat = path.stat()
            rel = str(path.relative_to(source_dir))
            found[rel] = DiscoveredFile(
                path=path,
                relative_path=rel,
                role=role,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            )
    return list(found.values())


def discover_release(
    data_root: Path | str,
    family: FamilyDef,
    release: ReleaseDef,
    *,
    explicit_dir: Path | str | None = None,
) -> DiscoveredRelease:
    root = Path(data_root)
    source_dir = Path(explicit_dir) if explicit_dir else (root / release.source_subdir)
    result = DiscoveredRelease(family=family, release=release, source_dir=source_dir)

    if not source_dir.is_dir():
        result.issues.append(f"source directory not found: {source_dir}")
        return result

    result.data_files = _scan(source_dir, release.data_globs, role="data")
    result.aux_files = _scan(source_dir, release.aux_globs, role="aux")

    if not result.data_files:
        result.issues.append(
            f"no data files matching {release.data_globs} under {source_dir}"
        )
        return result

    result.present = True
    return result


def discover_all(data_root: Path | str) -> list[DiscoveredRelease]:
    out: list[DiscoveredRelease] = []
    for fam in CATALOG_FAMILIES:
        if not fam.importer_available:
            continue
        for rel in fam.releases:
            out.append(discover_release(data_root, fam, rel))
    return out


def discover_one(
    data_root: Path | str,
    family_slug: str,
    release_slug: str,
    *,
    explicit_dir: Path | str | None = None,
) -> DiscoveredRelease:
    fam = get_family_def(family_slug)
    if fam is None:
        raise ValueError(f"Unknown family {family_slug!r}")
    rel = fam.release(release_slug)
    if rel is None:
        raise ValueError(
            f"Unknown release {release_slug!r} for family {family_slug!r} "
            f"(known: {[r.slug for r in fam.releases]})"
        )
    return discover_release(data_root, fam, rel, explicit_dir=explicit_dir)
