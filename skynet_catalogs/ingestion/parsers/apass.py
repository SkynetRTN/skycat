"""APASS DR6 (.sum) and DR10 (.txt) streaming parsers.

DR6 ``.sum`` columns (whitespace-separated)::

    Name RA(deg) raerr(") DEC(deg) decerr(") nobs mobs V (B-V) B g' r' i' \
        Verr (B-V)err Berr g'err r'err i'err

DR10 ``.txt`` columns (header line ``APASS ID …``)::

    APASS_ID ra raerr dec decerr  nobs(B V u g r i z Y)  mag(B V u g r i z Y) \
        magerr(B V u g r i z Y)

Both normalize to the canonical APASS column set. ``99.999`` magnitudes/errors
become NULL. Per-band counts / DR6 colour are kept in ``extra`` (JSONB).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from .base import ParseStats, open_text, to_float, to_int

# Canonical APASS data columns (must match models.apass.ApassSource minus
# release_id / id / geom). ``extra`` is always last.
APASS_COLUMNS: tuple[str, ...] = (
    "native_id", "ra_deg", "dec_deg", "ra_err_arcsec", "dec_err_arcsec", "n_obs_total",
    "johnson_b_mag", "johnson_b_err_mag", "johnson_v_mag", "johnson_v_err_mag",
    "sloan_u_mag", "sloan_u_err_mag", "sloan_g_mag", "sloan_g_err_mag",
    "sloan_r_mag", "sloan_r_err_mag", "sloan_i_mag", "sloan_i_err_mag",
    "sloan_z_mag", "sloan_z_err_mag", "y_mag", "y_err_mag", "extra",
)


class ApassDr6Parser:
    columns = APASS_COLUMNS
    source_format = "apass_dr6_sum"

    def iter_rows(self, paths: Iterable[Path], stats: ParseStats) -> Iterator[tuple]:
        for path in paths:
            with open_text(path) as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if not line or line.lstrip().startswith("#"):
                        continue
                    f = line.split()
                    if len(f) < 19:
                        stats.record_malformed(line)
                        continue
                    try:
                        native_id = f[0]
                        ra = float(f[1]); dec = float(f[3])
                    except ValueError:
                        stats.record_malformed(line)
                        continue
                    ra_err = to_float(f[2], missing_at_or_above=None)
                    dec_err = to_float(f[4], missing_at_or_above=None)
                    nobs = to_int(f[5]); mobs = to_int(f[6])
                    v = to_float(f[7]); bv = to_float(f[8])
                    b = to_float(f[9]); g = to_float(f[10]); r = to_float(f[11]); i = to_float(f[12])
                    verr = to_float(f[13], missing_at_or_above=None)
                    bverr = to_float(f[14], missing_at_or_above=None)
                    berr = to_float(f[15], missing_at_or_above=None)
                    gerr = to_float(f[16], missing_at_or_above=None)
                    rerr = to_float(f[17], missing_at_or_above=None)
                    ierr = to_float(f[18], missing_at_or_above=None)
                    extra = {"mobs": mobs, "b_minus_v_mag": bv, "b_minus_v_err_mag": bverr,
                             "release": "dr6"}
                    stats.parsed += 1
                    yield (
                        native_id, ra, dec, ra_err, dec_err, nobs,
                        b, berr, v, verr,
                        None, None, g, gerr,
                        r, rerr, i, ierr,
                        None, None, None, None, extra,
                    )


class ApassDr10Parser:
    columns = APASS_COLUMNS
    source_format = "apass_dr10_txt"

    def iter_rows(self, paths: Iterable[Path], stats: ParseStats) -> Iterator[tuple]:
        for path in paths:
            with open_text(path) as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    f = line.split()
                    if not f or not f[0][0].isdigit():
                        continue  # header line (e.g. "APASS ID ...")
                    if len(f) < 29:
                        stats.record_malformed(line)
                        continue
                    try:
                        native_id = f[0]
                        ra = float(f[1]); dec = float(f[3])
                    except ValueError:
                        stats.record_malformed(line)
                        continue
                    ra_err = to_float(f[2], missing_at_or_above=None)
                    dec_err = to_float(f[4], missing_at_or_above=None)
                    nobs = [to_int(x) for x in f[5:13]]          # B V u g r i z Y
                    mags = [to_float(x) for x in f[13:21]]        # B V u g r i z Y
                    errs = [to_float(x) for x in f[21:29]]        # B V u g r i z Y
                    b, v, u, g, r, i, z, y = mags
                    be, ve, ue, ge, re, ie, ze, ye = errs
                    extra = {
                        "nobs_per_band": dict(zip("BVugrizY", nobs)),
                        "release": "dr10",
                    }
                    stats.parsed += 1
                    yield (
                        native_id, ra, dec, ra_err, dec_err, None,
                        b, be, v, ve,
                        u, ue, g, ge,
                        r, re, i, ie,
                        z, ze, y, ye, extra,
                    )
