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


class TestRejectsIncomparableValues:
    """A type mismatch is caller input, so it is a `CatalogQueryError`.

    The allow-listing already works — the payload below arrives as a bound
    parameter, not as SQL (see below). What used to escape was a psycopg
    `ProgrammingError: operator does not exist: double precision < character
    varying`, which `api-stability.md` promises as `CatalogQueryError` and which
    a service fronting the reader would otherwise turn into a 500.
    """

    def test_string_against_a_numeric_column(self, table):
        with pytest.raises(CatalogQueryError, match="johnson_v_mag"):
            _quality_clause(
                table, QualityFilter("johnson_v_mag", "<", "'; DROP TABLE x; --")
            )

    def test_number_against_a_text_column(self, table):
        with pytest.raises(CatalogQueryError, match="native_id"):
            _quality_clause(table, QualityFilter("native_id", "=", 1))

    def test_jsonb_column_is_not_filterable(self, table):
        with pytest.raises(CatalogQueryError, match="extra"):
            _quality_clause(table, QualityFilter("extra", "=", "x"))

    def test_bool_is_not_a_number(self, table):
        """`isinstance(True, int)` is Python's opinion, not PostgreSQL's."""
        with pytest.raises(CatalogQueryError, match="n_obs_total"):
            _quality_clause(table, QualityFilter("n_obs_total", ">=", True))

    def test_int_is_a_fine_bound_for_a_float_column(self, table):
        """Widened where PostgreSQL's own comparison is — this must still work."""
        assert _quality_clause(table, QualityFilter("johnson_v_mag", "<", 15)) is not None


class TestValueIsNeverExecutableSql:
    def test_injection_payload_stays_a_bound_parameter(self, table):
        payload = "x'); DROP TABLE catalog_data.apass_source; --"
        compiled = _quality_clause(
            table, QualityFilter("native_id", "=", payload)
        ).compile()
        assert "DROP TABLE" not in str(compiled)
        assert payload in compiled.params.values()
