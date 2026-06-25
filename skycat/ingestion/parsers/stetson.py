"""Stetson globular-cluster ``table4.dat`` streaming parser.

The author's updated ``table4.dat`` is the CDS fixed-width record with ``|`` in
the inter-column gaps, i.e. 24 pipe-delimited fields per line::

    Cluster | Star | Xpos | Ypos | Umag | e_Umag | o_Umag | Bmag | e_Bmag | o_Bmag
    | Vmag | e_Vmag | o_Vmag | Rmag | e_Rmag | o_Rmag | Imag | e_Imag | o_Imag
    | chi | sharp | vary | weight | "RAh RAm RAs sDEd DEm DEs"

The trailing field is the J2000 sexagesimal position (sign attached to the
degrees). Coordinates convert to degrees exactly as the remote VizieR provider
does. Blank magnitude / error fields become NULL (never 0); negative
declinations keep their sign. ``native_id`` is ``str(Star)`` (unique only within
a cluster, matching the remote provider); the cluster name is stored separately
for field-name filtering. Streams one row at a time over the ~4.9M-row file with
bounded memory.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from .base import ParseStats, open_text, to_float, to_int

# Must match models.stetson.StetsonSource minus release_id / id / geom; extra last.
STETSON_COLUMNS: tuple[str, ...] = (
    "native_id",
    "cluster",
    "ra_deg",
    "dec_deg",
    "johnson_u_mag",
    "johnson_u_err_mag",
    "n_obs_u",
    "johnson_b_mag",
    "johnson_b_err_mag",
    "n_obs_b",
    "johnson_v_mag",
    "johnson_v_err_mag",
    "n_obs_v",
    "cousins_r_mag",
    "cousins_r_err_mag",
    "n_obs_r",
    "cousins_i_mag",
    "cousins_i_err_mag",
    "n_obs_i",
    "chi",
    "sharp",
    "variability_index",
    "variability_weight",
    "extra",
)

_N_FIELDS = 24  # 24 pipe-delimited columns


def _radec(coord: str) -> tuple[float, float]:
    """Parse "RAh RAm RAs sDEd DEm DEs" -> (ra_deg, dec_deg)."""
    parts = coord.split()
    if len(parts) != 6:
        raise ValueError(f"bad coordinate field {coord!r}")
    rah, ram, ras, ded, dem, des = parts
    ra_deg = (int(rah) + int(ram) / 60.0 + float(ras) / 3600.0) * 15.0
    sign = -1.0 if ded.strip().startswith("-") else 1.0
    dec_deg = sign * (abs(int(ded)) + int(dem) / 60.0 + float(des) / 3600.0)
    return ra_deg, dec_deg


class StetsonGlobsParser:
    columns = STETSON_COLUMNS
    source_format = "stetson_globs_dat"

    def iter_rows(self, paths: Iterable[Path], stats: ParseStats) -> Iterator[tuple]:
        for path in paths:
            with open_text(path) as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if not line.strip():
                        continue
                    f = line.split("|")
                    if len(f) != _N_FIELDS:
                        stats.record_malformed(line)
                        continue
                    cluster = f[0].strip()
                    star = f[1].strip()
                    if not cluster or not star:
                        stats.record_malformed(line)
                        continue
                    try:
                        native_id = str(int(star))
                        ra_deg, dec_deg = _radec(f[23])
                    except ValueError:
                        stats.record_malformed(line)
                        continue

                    # Blank magnitudes/errors -> None (NULL); no numeric sentinel.
                    u = to_float(f[4], missing_at_or_above=None)
                    eu = to_float(f[5], missing_at_or_above=None)
                    nu = to_int(f[6])
                    b = to_float(f[7], missing_at_or_above=None)
                    eb = to_float(f[8], missing_at_or_above=None)
                    nb = to_int(f[9])
                    v = to_float(f[10], missing_at_or_above=None)
                    ev = to_float(f[11], missing_at_or_above=None)
                    nv = to_int(f[12])
                    r = to_float(f[13], missing_at_or_above=None)
                    er = to_float(f[14], missing_at_or_above=None)
                    nr = to_int(f[15])
                    i = to_float(f[16], missing_at_or_above=None)
                    ei = to_float(f[17], missing_at_or_above=None)
                    ni = to_int(f[18])
                    chi = to_float(f[19], missing_at_or_above=None)
                    sharp = to_float(f[20], missing_at_or_above=None)
                    vary = to_float(f[21], missing_at_or_above=None)
                    weight = to_float(f[22], missing_at_or_above=None)
                    xpos = to_float(f[2], missing_at_or_above=None)
                    ypos = to_float(f[3], missing_at_or_above=None)

                    extra = {"xpos_arcsec": xpos, "ypos_arcsec": ypos}
                    extra = {k: val for k, val in extra.items() if val is not None}

                    stats.parsed += 1
                    yield (
                        native_id, cluster, ra_deg, dec_deg,
                        u, eu, nu, b, eb, nb, v, ev, nv, r, er, nr, i, ei, ni,
                        chi, sharp, vary, weight,
                        extra or None,
                    )
