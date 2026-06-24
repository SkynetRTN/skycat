"""Parser base protocol + shared parsing helpers.

Parsers are **streaming**: they yield one normalized row tuple at a time across
(potentially many) source files, with bounded memory — never loading a whole
catalog into RAM. Each row tuple is aligned to the parser's ``columns`` (the
data columns of the family's production table, ``extra`` JSONB last).
"""

from __future__ import annotations

import gzip
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol


@dataclass
class ParseStats:
    parsed: int = 0          # rows successfully parsed
    malformed: int = 0       # lines that could not be parsed at all
    malformed_examples: list[str] | None = None

    def record_malformed(self, line: str) -> None:
        self.malformed += 1
        if self.malformed_examples is None:
            self.malformed_examples = []
        if len(self.malformed_examples) < 20:
            self.malformed_examples.append(line[:200])


class CatalogParser(Protocol):
    columns: tuple[str, ...]
    source_format: str

    def iter_rows(self, paths: Iterable[Path], stats: ParseStats) -> Iterator[tuple]:
        ...


def open_text(path: Path) -> IO[str]:
    """Open a possibly-gzipped text file for streaming reads (latin-1 tolerant)."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="latin-1", newline="")
    return open(path, "rt", encoding="latin-1", newline="")


def to_float(token: str, *, missing_at_or_above: float | None = 90.0) -> float | None:
    """Parse a float token; treat blanks and sentinels (>= threshold) as missing.

    APASS uses ``99.999`` for absent magnitudes/errors; VSX leaves fields blank.
    Returns ``None`` for missing so NULL is preserved (never coerced to 0).
    """
    token = token.strip()
    if not token:
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    if missing_at_or_above is not None and value >= missing_at_or_above:
        return None
    return value


def to_int(token: str) -> int | None:
    token = token.strip()
    if not token:
        return None
    try:
        return int(token)
    except ValueError:
        try:
            return int(float(token))
        except ValueError:
            return None
