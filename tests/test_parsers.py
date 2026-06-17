"""Parser tests against the committed sample fixtures (real source formats)."""

from __future__ import annotations

from pathlib import Path

from skynet_catalogs.ingestion.parsers import ParseStats, get_parser

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
