"""Invariants of the catalog family/release definitions.

Pure Python — no database. These assertions were previously stranded in the
`postgis`-marked integration module, so they only ran when a catalog DB happened
to be reachable; a statement about `catalog_defs.py` should hold for anyone
running the unit suite.
"""

from __future__ import annotations

import pytest

from skycat.registry.catalog_defs import CATALOG_FAMILIES, get_family_def

SHIPPED_FAMILIES = ("apass", "vsx", "landolt", "stetson")


@pytest.mark.parametrize("slug", SHIPPED_FAMILIES)
def test_every_release_carries_an_expected_row_count(slug):
    """Without a published size, an import has nothing to be checked against.

    The row-count guard is what catches a truncated source, so a family missing
    it silently loses that protection.
    """
    fam = get_family_def(slug)
    assert fam is not None
    assert fam.releases, slug
    for rel in fam.releases:
        assert rel.approx_row_count, f"{slug}/{rel.slug} has no approx_row_count"


@pytest.mark.parametrize("slug", SHIPPED_FAMILIES)
def test_shipped_families_have_an_importer(slug):
    assert get_family_def(slug).importer_available is True


def test_no_speculative_families_remain():
    """Families are added when an importer lands, not in advance."""
    assert {f.slug for f in CATALOG_FAMILIES} == set(SHIPPED_FAMILIES)
    assert all(f.importer_available for f in CATALOG_FAMILIES)
