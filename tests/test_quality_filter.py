"""Quality filters are validated against allow-lists and bind their values.

These run without a database: the clause is built and compiled in-process, so
the assertions are about the generated SQL rather than query results.
"""

from __future__ import annotations

import pytest

import skycat.models  # noqa: F401 -- registers the data tables on CatalogBase.metadata
from skycat.query import CatalogQueryError, QualityFilter
from skycat.query.cone import _data_table, _quality_clause
from skycat.registry.catalog_defs import get_family_def


@pytest.fixture(scope="module")
def table():
    return _data_table(get_family_def("apass").data_table)


class TestAcceptsValidFilters:
    @pytest.mark.parametrize("op", ["=", "!=", "<", "<=", ">", ">="])
    def test_every_allowed_operator(self, table, op):
        clause = _quality_clause(table, QualityFilter("n_obs_total", op, 3))
        assert clause is not None

    def test_value_is_bound_not_interpolated(self, table):
        compiled = _quality_clause(
            table, QualityFilter("n_obs_total", ">=", 3)
        ).compile()
        assert list(compiled.params.values()) == [3]
        assert "3" not in str(compiled)


class TestRejectsBadIdentifiers:
    def test_unknown_column(self, table):
        with pytest.raises(CatalogQueryError, match="Unknown quality-filter column"):
            _quality_clause(table, QualityFilter("not_a_column", "=", 1))

    def test_sql_injected_via_column(self, table):
        with pytest.raises(CatalogQueryError, match="Unknown quality-filter column"):
            _quality_clause(
                table, QualityFilter("1=1; DROP TABLE catalog_data.apass_source; --", "=", 1)
            )

    def test_unsupported_operator(self, table):
        with pytest.raises(CatalogQueryError, match="Unsupported quality-filter operator"):
            _quality_clause(table, QualityFilter("n_obs_total", "LIKE", 1))

    def test_sql_injected_via_operator(self, table):
        with pytest.raises(CatalogQueryError, match="Unsupported quality-filter operator"):
            _quality_clause(table, QualityFilter("n_obs_total", ">= 1 OR 1=1 --", 1))

    def test_spatial_column_is_off_limits(self, table):
        with pytest.raises(CatalogQueryError, match="spatial column"):
            _quality_clause(table, QualityFilter("geom", "=", "POINT(0 0)"))


class TestValueIsNeverExecutableSql:
    def test_injection_payload_stays_a_bound_parameter(self, table):
        payload = "x'); DROP TABLE catalog_data.apass_source; --"
        compiled = _quality_clause(
            table, QualityFilter("native_id", "=", payload)
        ).compile()
        assert "DROP TABLE" not in str(compiled)
        assert payload in compiled.params.values()
