"""Order-by columns are validated against the same allow-list as quality filters.

These run without a database: the clause is built and compiled in-process, so
the assertions are about the generated SQL rather than query results. The
DB-backed behaviour (brightest-N != nearest-N under a limit) is covered in
test_integration.py.
"""

from __future__ import annotations

import pytest

import skycat.models  # noqa: F401 -- registers the data tables on CatalogBase.metadata
from skycat.query import CatalogQueryError
from skycat.query.cone import _data_table, _order_by_clause
from skycat.registry.catalog_defs import get_family_def


@pytest.fixture(scope="module")
def table():
    return _data_table(get_family_def("apass").data_table)


class TestAcceptsNumericColumns:
    @pytest.mark.parametrize(
        "column", ["johnson_v_mag", "johnson_b_mag", "n_obs_total", "ra_deg"]
    )
    def test_numeric_column(self, table, column):
        assert _order_by_clause(table, column) is not None

    def test_ascending_nulls_last(self, table):
        """Brightest-first, and unmeasured rows never displace real matches."""
        compiled = str(_order_by_clause(table, "johnson_v_mag").compile())
        assert "johnson_v_mag ASC" in compiled
        assert "NULLS LAST" in compiled


class TestRejectsBadIdentifiers:
    def test_unknown_column(self, table):
        with pytest.raises(CatalogQueryError, match="Unknown order-by column"):
            _order_by_clause(table, "not_a_column")

    def test_non_numeric_column(self, table):
        with pytest.raises(CatalogQueryError, match="is not numeric"):
            _order_by_clause(table, "native_id")

    def test_spatial_column_is_off_limits(self, table):
        with pytest.raises(CatalogQueryError, match="spatial column"):
            _order_by_clause(table, "geom")

    def test_sql_injected_via_column(self, table):
        with pytest.raises(CatalogQueryError, match="Unknown order-by column"):
            _order_by_clause(table, "johnson_v_mag; DROP TABLE catalog_data.apass_source; --")
