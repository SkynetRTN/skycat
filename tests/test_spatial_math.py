"""Spherical-geometry helpers (pure Python)."""

from __future__ import annotations

import pytest

from skycat.spatial import (
    angular_separation_deg,
    degrees_to_meters,
    is_valid_dec,
    is_valid_ra,
    meters_to_degrees,
    ra_to_lon,
    validate_radec,
)


@pytest.mark.parametrize(
    "ra,expected",
    [
        (0.0, 0.0),
        (10.0, 10.0),
        (180.0, 180.0),
        (180.1, -179.9),
        (270.0, -90.0),
        (359.9, -0.1),
    ],
)
def test_ra_to_lon(ra, expected):
    assert ra_to_lon(ra) == pytest.approx(expected)


def test_degrees_meters_roundtrip():
    for deg in (0.001, 0.1, 1.0, 5.0, 90.0):
        assert meters_to_degrees(degrees_to_meters(deg)) == pytest.approx(
            deg, rel=1e-12
        )


def test_angular_separation_known():
    # 1 degree apart along the equator.
    assert angular_separation_deg(10.0, 0.0, 11.0, 0.0) == pytest.approx(1.0, abs=1e-9)
    # Across the RA 0/360 boundary: 359.5 and 0.5 are 1 degree apart.
    assert angular_separation_deg(359.5, 0.0, 0.5, 0.0) == pytest.approx(1.0, abs=1e-9)
    # Near the pole: two points at dec 89.9, RA 180 apart are 0.2 deg apart.
    assert angular_separation_deg(0.0, 89.9, 180.0, 89.9) == pytest.approx(
        0.2, abs=1e-6
    )


def test_validation_ranges():
    assert is_valid_ra(0.0) and is_valid_ra(359.999)
    assert not is_valid_ra(360.0) and not is_valid_ra(-0.1)
    assert is_valid_dec(-90.0) and is_valid_dec(90.0)
    assert not is_valid_dec(90.1) and not is_valid_dec(-90.1)
    validate_radec(123.0, -45.0)
    with pytest.raises(ValueError):
        validate_radec(400.0, 0.0)
    with pytest.raises(ValueError):
        validate_radec(0.0, 91.0)
