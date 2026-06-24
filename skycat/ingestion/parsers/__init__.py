"""Parser registry, keyed by source format."""

from __future__ import annotations

from .apass import APASS_COLUMNS, ApassDr6Parser, ApassDr10Parser
from .base import CatalogParser, ParseStats
from .vsx import VSX_COLUMNS, VsxParser

_PARSERS: dict[str, type] = {
    ApassDr6Parser.source_format: ApassDr6Parser,
    ApassDr10Parser.source_format: ApassDr10Parser,
    VsxParser.source_format: VsxParser,
}


def get_parser(source_format: str) -> CatalogParser:
    cls = _PARSERS.get(source_format)
    if cls is None:
        raise ValueError(f"No parser for source format {source_format!r}")
    return cls()


def supported_formats() -> list[str]:
    return sorted(_PARSERS)


__all__ = [
    "APASS_COLUMNS",
    "ApassDr6Parser",
    "ApassDr10Parser",
    "CatalogParser",
    "ParseStats",
    "VSX_COLUMNS",
    "VsxParser",
    "get_parser",
    "supported_formats",
]
