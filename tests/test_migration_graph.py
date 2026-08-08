"""The Alembic revision graph must stay singular and loadable. No database.

Two heads is the classic merge accident: two branches each add a migration on
top of the same parent, the merge is clean because the files do not overlap, and
nothing complains until `alembic upgrade head` refuses to pick a head — in CI,
against staging, or in the middle of a production migration Job.

A database-free check catches it on the branch instead. This also loads every
revision module, so a revision that no longer imports (a renamed helper, a
deleted constant) fails here rather than mid-upgrade with a half-applied schema.
"""

from __future__ import annotations

from alembic.script import ScriptDirectory

from skycat.config import CatalogDatabaseConfig
from skycat.database.migrate import make_alembic_config


def _script() -> ScriptDirectory:
    # A config, not a connection: ScriptDirectory only reads the filesystem, so
    # the URL here is never dialed.
    return ScriptDirectory.from_config(make_alembic_config(CatalogDatabaseConfig()))


def test_exactly_one_head():
    heads = _script().get_heads()
    assert len(heads) == 1, (
        f"expected exactly one Alembic head, found {len(heads)}: {heads}. "
        "Two heads means the revision graph forked — merge them with "
        "`alembic merge` or re-parent the newer revision."
    )


def test_every_revision_loads_and_reaches_base():
    script = _script()
    revisions = list(script.walk_revisions())
    assert revisions, "no Alembic revisions found"

    # walk_revisions() imports each module, so a broken revision raises here.
    # Walking head->base also proves the chain has no missing down_revision.
    assert revisions[-1].down_revision is None, (
        f"the oldest reachable revision {revisions[-1].revision!r} has "
        f"down_revision={revisions[-1].down_revision!r}; the chain from head "
        "does not reach base"
    )


def test_no_duplicate_revision_ids():
    ids = [rev.revision for rev in _script().walk_revisions()]
    assert len(ids) == len(set(ids)), f"duplicate revision ids: {ids}"
