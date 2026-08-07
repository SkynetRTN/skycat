"""Caller input the query layer rejects, and the type it rejects it with.

`api-stability.md` lists `CatalogQueryError` as a stable type that "keeps … the
situations that raise them", and `CatalogReader` is the supported entry point —
so a service that maps `CatalogQueryError` to HTTP 400 must not get a bare
`ValueError` from `validate_radec` or a psycopg `DataError: LIMIT must not be
negative` for input it was handed.

No database: every check below happens before an engine is built. The settings
point at a closed port so that a regression fails immediately instead of dialing
whatever `SKYCAT_DB_*` happens to name.
"""

from __future__ import annotations

import socket

import pytest

from sqlalchemy.exc import OperationalError

import skycat.models  # noqa: F401 -- registers the data tables on CatalogBase.metadata
from skycat.config import CatalogDatabaseConfig, CatalogSettings
from skycat.query import CatalogQueryError, cone_search, lookup_native_id
from skycat.query.cone import cone_search_plan


@pytest.fixture(scope="module")
def settings() -> CatalogSettings:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead_port = sock.getsockname()[1]
    return CatalogSettings(
        base=CatalogDatabaseConfig(host="127.0.0.1", port=dead_port, name="catalogs"),
        default_user="catalog_reader", default_password="",
        bootstrap_user="", bootstrap_password="", admin_user="", admin_password="",
        ingest_user="", ingest_password="", reader_user="", reader_password="",
        data_root="/nonexistent", work_root="/nonexistent",
    )


class TestCoordinateRange:
    """`skycat.spatial` keeps its bare `ValueError` — it is dependency-free and
    shared with the parsers. The query layer translates at its own boundary."""

    def test_ra_above_360(self, settings):
        with pytest.raises(CatalogQueryError, match="RA out of range"):
            cone_search(settings, "apass", 400.0, 0.0, radius_deg=0.1)

    def test_dec_below_minus_90(self, settings):
        with pytest.raises(CatalogQueryError, match="Dec out of range"):
            cone_search(settings, "apass", 10.0, -91.0, radius_deg=0.1)

    def test_explain_validates_the_same_centre(self, settings):
        with pytest.raises(CatalogQueryError, match="RA out of range"):
            cone_search_plan(settings, "apass", 400.0, 0.0, radius_deg=0.1)


class TestLimit:
    def test_negative_limit_in_cone_search(self, settings):
        with pytest.raises(CatalogQueryError, match="limit"):
            cone_search(settings, "apass", 10.0, 0.0, radius_deg=0.1, limit=-1)

    def test_negative_limit_in_lookup(self, settings):
        with pytest.raises(CatalogQueryError, match="limit"):
            lookup_native_id(settings, "apass", "0020120136", limit=-1)

    def test_zero_is_allowed(self, settings):
        """`LIMIT 0` is a legitimate "just tell me the shape" query and
        PostgreSQL accepts it; only a negative is the caller's mistake. It gets
        past validation and dies on the deliberately closed port instead.
        """
        with pytest.raises(OperationalError):
            cone_search(settings, "apass", 10.0, 0.0, radius_deg=0.1, limit=0)
