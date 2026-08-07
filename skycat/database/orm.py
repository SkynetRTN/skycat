"""Typed helpers for fetches whose absence is a bug, not a case to handle.

``Session.get()`` returns ``T | None`` because a primary key may not exist. Most
of this package's lookups are not that: the row was created and committed a few
lines earlier in the same function, and ``None`` would mean another writer
deleted a release mid-import. Threading an ``if x is None`` branch through every
one of those sites adds unreachable code and hides the two places where absence
really is expected.

``require_row`` states the invariant once, keeps the type checker's narrowing,
and turns a violation into a named error instead of ``AttributeError: 'NoneType'
object has no attribute 'state'`` several frames from the cause.
"""

from __future__ import annotations

from typing import TypeVar

__all__ = ["MissingRowError", "require_row"]

T = TypeVar("T")


class MissingRowError(LookupError):
    """A row the caller had already established must exist was not found.

    Always a consistency failure — a concurrent delete, a rolled-back
    transaction, or a caller passing an id it never verified.
    """


def require_row(row: T | None, what: str) -> T:
    """Return ``row``, or raise :class:`MissingRowError` naming what was missing.

    ``what`` should identify the row well enough to act on, e.g.
    ``f"catalog_release id={release_id}"``.
    """
    if row is None:
        raise MissingRowError(f"{what} not found")
    return row
