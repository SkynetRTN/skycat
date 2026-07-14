"""Parser registry, keyed by source format."""

from __future__ import annotations

from .apass import APASS_COLUMNS, ApassDr6Parser, ApassDr10Parser
from .base import CatalogParser, ParseStats
from .landolt import (
    LANDOLT_COLUMNS,
    Landolt1992Parser,
    Landolt2009Parser,
)
from .stetson import STETSON_COLUMNS, StetsonGlobsParser
from .vsx import VSX_COLUMNS, VsxParser

_PARSERS: dict[str, type] = {
    ApassDr6Parser.source_format: ApassDr6Parser,
    ApassDr10Parser.source_format: ApassDr10Parser,
    Landolt1992Parser.source_format: Landolt1992Parser,
    Landolt2009Parser.source_format: Landolt2009Parser,
    StetsonGlobsParser.source_format: StetsonGlobsParser,
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
    "LANDOLT_COLUMNS",
    "Landolt1992Parser",
    "Landolt2009Parser",
    "ParseStats",
    "STETSON_COLUMNS",
    "StetsonGlobsParser",
    "VSX_COLUMNS",
    "VsxParser",
    "get_parser",
    "supported_formats",
]
