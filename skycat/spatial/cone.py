"""Spherical-geometry helpers shared by the models, ingestion, and query layers.

Coordinate model
----------------
* Catalog tables keep the *original* ``ra_deg`` (0..360) and ``dec_deg``
  (-90..90) numeric columns.
* A derived ``geom geography(Point, 4326)`` column is GENERATED from them with::

      ST_SetSRID(ST_MakePoint(
          CASE WHEN ra_deg > 180 THEN ra_deg - 360 ELSE ra_deg END,
          dec_deg), 4326)

  i.e. RA is mapped to PostGIS longitude in [-180, 180] and Dec maps straight to
  latitude. Because the geometry is implicitly cast to ``geography`` and indexed
  with GiST, cone searches are spherical: RA wraparound at 0/360 and the
  celestial poles are handled correctly with no flat-plane approximation.

Distances
---------
``ST_DWithin``/``ST_Distance`` on ``geography`` take/return **metres**. We always
pass ``use_spheroid => false`` so PostGIS evaluates them on a sphere of radius
:data:`POSTGIS_SPHERE_RADIUS_M`. Converting angular degrees <-> metres with that
same radius makes the round-trip exact (true angular separation).
"""

from __future__ import annotations

import math

from geoalchemy2 import Geography
from sqlalchemy import Float, cast, func
from sqlalchemy.sql.elements import ColumnElement

from ..constants import (
    DEC_MAX_DEG,
    DEC_MIN_DEG,
    POSTGIS_SPHERE_RADIUS_M,
    RA_MAX_DEG,
    RA_MIN_DEG,
    SRID,
)

# SQL expression that derives the geography point from ra_deg/dec_deg columns.
# Reused verbatim by every positional table's GENERATED column so the mapping is
# defined in exactly one place.
GEOM_GENERATED_EXPR = (
    "(ST_SetSRID(ST_MakePoint("
    "CASE WHEN ra_deg > 180 THEN ra_deg - 360 ELSE ra_deg END, dec_deg), "
    f"{SRID}))::geography"
)


# --- pure-python helpers (used by parsers/validation/tests) -------------------
def ra_to_lon(ra_deg: float) -> float:
    """Map RA (0..360) to PostGIS longitude (-180..180)."""
    return ra_deg - 360.0 if ra_deg > 180.0 else ra_deg


def degrees_to_meters(angle_deg: float) -> float:
    return math.radians(angle_deg) * POSTGIS_SPHERE_RADIUS_M


def meters_to_degrees(distance_m: float) -> float:
    return math.degrees(distance_m / POSTGIS_SPHERE_RADIUS_M)


def angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle angular separation in degrees (haversine).

    Reference implementation used by tests to check the SQL ST_Distance result.
    """
    lon1, lat1, lon2, lat2 = map(math.radians, (ra1, dec1, ra2, dec2))
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a))))


def is_valid_ra(ra_deg: float) -> bool:
    return RA_MIN_DEG <= ra_deg < RA_MAX_DEG


def is_valid_dec(dec_deg: float) -> bool:
    return DEC_MIN_DEG <= dec_deg <= DEC_MAX_DEG


def validate_radec(ra_deg: float, dec_deg: float) -> None:
    if not is_valid_ra(ra_deg):
        raise ValueError(f"RA out of range [0, 360): {ra_deg}")
    if not is_valid_dec(dec_deg):
        raise ValueError(f"Dec out of range [-90, 90]: {dec_deg}")


# --- SQLAlchemy expression helpers (used by query services) ------------------
def search_point(ra_deg: float, dec_deg: float) -> ColumnElement:
    """A ``geography(Point,4326)`` expression for the search centre."""
    lon = ra_to_lon(ra_deg)
    return cast(func.ST_SetSRID(func.ST_MakePoint(lon, dec_deg), SRID), Geography)


def within_radius(geom_col: ColumnElement, ra_deg: float, dec_deg: float, radius_deg: float) -> ColumnElement:
    """``ST_DWithin`` predicate (spherical) for an indexed cone search."""
    radius_m = degrees_to_meters(radius_deg)
    return func.ST_DWithin(geom_col, search_point(ra_deg, dec_deg), radius_m, False)


def separation_deg_expr(geom_col: ColumnElement, ra_deg: float, dec_deg: float) -> ColumnElement:
    """Angular separation (degrees) between ``geom_col`` and the search centre."""
    distance_m = func.ST_Distance(geom_col, search_point(ra_deg, dec_deg), False)
    return cast(distance_m, Float) / POSTGIS_SPHERE_RADIUS_M * (180.0 / math.pi)
