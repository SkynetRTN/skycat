"""Parser tests against the committed sample fixtures (real source formats)."""

from __future__ import annotations

from pathlib import Path

import pytest

from skycat.ingestion.parsers import ParseStats, get_parser

DATA = Path(__file__).parent / "data"


def _rows(fmt: str, filename: str):
    parser = get_parser(fmt)
    stats = ParseStats()
    rows = list(parser.iter_rows([DATA / filename], stats))
    cols = parser.columns
    return [dict(zip(cols, r)) for r in rows], stats


def test_apass_dr6_parsing():
    rows, stats = _rows("apass_dr6_sum", "apass_dr6_sample.sum")
    assert stats.parsed == len(rows) >= 6
    first = rows[0]
    assert first["native_id"] == "0020120136"
    assert first["ra_deg"] == 0.000236
    assert first["dec_deg"] == 1.886943
    # DR6: B/V/g'/r'/i' typed; u/z/Y absent (None).
    assert first["johnson_v_mag"] == 15.324
    assert first["johnson_b_mag"] == 16.411
    assert first["sloan_u_mag"] is None and first["y_mag"] is None
    # B-V preserved in extra.
    assert first["extra"]["b_minus_v_mag"] == 1.087
    # The deliberately-bad row (RA 999.9) is parsed (rejection happens in DB).
    assert any(r["ra_deg"] > 360 for r in rows)


def test_apass_dr10_parsing_and_sentinel():
    rows, stats = _rows("apass_dr10_txt", "apass_dr10_sample.txt")
    assert stats.parsed == len(rows) >= 6
    first = rows[0]
    assert first["native_id"] == "090-0000001"
    assert first["johnson_b_mag"] == 15.579
    assert first["johnson_v_mag"] == 14.798
    # 99.999 sentinels -> NULL (u, z, Y here).
    assert first["sloan_u_mag"] is None
    assert first["sloan_z_mag"] is None and first["y_mag"] is None
    # per-band nobs preserved in extra
    assert first["extra"]["nobs_per_band"]["B"] == 4


def test_apass_dr10_counts_garbage_instead_of_dropping_it(tmp_path):
    """"Nothing is silently dropped" has to hold for DR10's header skip too.

    The skip used to be "first token does not start with a digit", which is also
    true of a truncated write, a stray header concatenated mid-stream, or a
    filesystem-mangled line — none of which appeared in `parsed`, in `malformed`,
    or in the rejects table. Only the header is a header.
    """
    src = tmp_path / "apass_dr10_fragment.txt"
    good = (DATA / "apass_dr10_sample.txt").read_text().splitlines()
    header, first_row = good[0], good[1]
    src.write_text(
        f"{header}\n{first_row}\n"
        "*** transfer aborted ***\n"           # a truncated/corrupt line
        f"{header}\n"                          # a header again, mid-stream
        f"{first_row}\n"
    )
    parser = get_parser("apass_dr10_txt")
    stats = ParseStats()
    rows = list(parser.iter_rows([src], stats))

    assert len(rows) == stats.parsed == 2      # both real rows, neither header
    assert stats.malformed == 1
    assert stats.malformed_examples and "transfer aborted" in stats.malformed_examples[0]


def test_vsx_fixed_width_parsing():
    rows, stats = _rows("vsx_dat", "vsx_sample.dat")
    assert stats.parsed == len(rows) >= 6
    first = rows[0]
    assert first["vsx_oid"] == 8278100
    assert first["native_id"] == "8278100"
    assert first["name"].startswith("Gaia DR3")
    assert first["dec_deg"] == -75.86906
    assert first["var_flag"] == 0
    # a row with a period
    assert any(r["period_days"] for r in rows)


def test_landolt_1992_parsing():
    rows, stats = _rows("landolt_1992_dat", "landolt_1992_sample.dat")
    # one deliberately-malformed line ("BADROW") is rejected, not silently skipped.
    assert stats.malformed == 1
    by_id = {r["native_id"]: r for r in rows}
    a = by_id["TPHE A"]
    assert a["ra_deg"] == 7.5375  # 00h30m09s
    assert a["dec_deg"] == pytest.approx(-46.522778, abs=1e-5)  # sign preserved
    assert a["johnson_v_mag"] == 14.651
    assert a["b_minus_v_mag"] == 0.793
    assert a["u_minus_b_mag"] == 0.380
    assert a["n_obs"] == 29 and a["n_nights"] == 12
    assert a["extra"]["release"] == "1992"
    # RA near 0h and near 24h both parse into [0, 360).
    assert by_id["RZERO A"]["ra_deg"] == pytest.approx(0.05, abs=1e-6)
    assert by_id["RWRAP B"]["ra_deg"] == pytest.approx(359.958333, abs=1e-5)
    assert by_id["RWRAP B"]["dec_deg"] == pytest.approx(-0.0375, abs=1e-6)  # tiny -dec
    # leading-zero/spaced designation kept as text; duplicate id preserved (2 rows).
    assert sum(1 for r in rows if r["native_id"] == "92 309") == 2


def test_landolt_2009_parsing_missing_errors():
    rows, stats = _rows("landolt_2009_dat", "landolt_2009_sample.dat")
    assert stats.malformed == 0
    by_id = {r["native_id"]: r for r in rows}
    # 2009 RA seconds + Dec arcsec carry decimals (distinct layout from 1992).
    i = by_id["TPhe I"]
    assert i["ra_deg"] == pytest.approx(7.5191375, abs=1e-6)
    assert i["johnson_v_mag"] == 14.820
    # blank error fields -> None (NULL), never 0.
    m = by_id["MISSERR A"]
    assert m["johnson_v_mag"] == 13.574
    assert m["johnson_v_err_mag"] is None
    assert m["b_minus_v_err_mag"] is None
    assert m["extra"]["release"] == "2009"


def test_stetson_globs_parsing():
    rows, stats = _rows("stetson_globs_dat", "stetson_globs_sample.dat")
    assert stats.malformed == 1  # the 4-field "BADSTET" line
    # E3 rows: U and R bands are blank -> None (partial-band rows), B/V/I present.
    e3 = [r for r in rows if r["cluster"] == "E3"]
    assert e3 and e3[0]["native_id"] == "4"
    assert e3[0]["johnson_u_mag"] is None and e3[0]["cousins_r_mag"] is None
    assert e3[0]["johnson_b_mag"] == 19.457 and e3[0]["johnson_v_mag"] == 18.514
    assert e3[0]["extra"]["xpos_arcsec"] == -1069.64
    # northern cluster (positive dec) with all five bands.
    m13 = [r for r in rows if r["cluster"] == "NGC6205"][0]
    assert m13["native_id"] == "1741"
    assert m13["dec_deg"] == pytest.approx(36.374972, abs=1e-5)
    assert m13["johnson_u_mag"] == 17.880 and m13["cousins_r_mag"] == 17.200
    # Star id repeats across clusters (unique only within a field).
    assert sum(1 for r in rows if r["native_id"] == "1741") == 2
    assert {r["cluster"] for r in rows if r["native_id"] == "1741"} == {"NGC6205", "NGC288"}
