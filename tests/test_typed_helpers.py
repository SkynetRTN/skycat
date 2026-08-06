"""The helpers that let the null-safety pyright rules run as errors. No database.

``require_row`` and ``radius_to_deg`` both exist to make an invariant explicit
instead of implicit, and both are on paths where a silent wrong answer is worse
than an exception: a vanished registry row, or a cone search that quietly
searched 60x the requested area.
"""

from __future__ import annotations

import pytest

from skycat.database.orm import MissingRowError, require_row
from skycat.query import radius_to_deg
from skycat.query.cone import CatalogQueryError


def test_require_row_passes_a_present_row_through_unchanged():
    row = object()
    assert require_row(row, "catalog_release id=1") is row


def test_require_row_names_what_was_missing():
    with pytest.raises(MissingRowError, match="catalog_release id=17"):
        require_row(None, "catalog_release id=17")


def test_missing_row_is_a_lookup_error():
    # Callers that already handle LookupError should not have to learn a new
    # exception type to keep working.
    assert issubclass(MissingRowError, LookupError)


@pytest.mark.parametrize(
    "kwargs,expected_deg",
    [
        ({"radius_deg": 0.25}, 0.25),
        ({"radius_arcmin": 15.0}, 0.25),
        ({"radius_arcsec": 900.0}, 0.25),
        ({"radius_arcsec": 1.0}, 1.0 / 3600.0),
        ({"radius_arcmin": 0.0}, 0.0),  # zero is a value, not "unset"
    ],
)
def test_radius_to_deg_converts_each_unit(kwargs, expected_deg):
    assert radius_to_deg(**kwargs) == pytest.approx(expected_deg)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"radius_deg": 1.0, "radius_arcmin": 1.0},
        {"radius_deg": 1.0, "radius_arcsec": 1.0},
        {"radius_deg": 1.0, "radius_arcmin": 1.0, "radius_arcsec": 1.0},
    ],
)
def test_radius_to_deg_requires_exactly_one_unit(kwargs):
    # Silently preferring one unit over another would turn a caller's typo into
    # a cone of the wrong size that still returns plausible-looking rows.
    with pytest.raises(CatalogQueryError, match="exactly one"):
        radius_to_deg(**kwargs)
