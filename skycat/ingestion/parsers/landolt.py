"""Landolt UBVRI standard-star ``table2.dat`` fixed-width streaming parsers.

Two releases share the same canonical column set but use *different* byte
layouts (the 1992 ``II/183A`` table uses integer RA seconds and a different
declination field block than the 2009 ``J/AJ/137/4186`` table, whose RA seconds
and Dec arcseconds carry decimals), so each release has its own byte map. Both
normalize to ``LandoltSource``'s data columns: the designation, J2000 position
in degrees, ``V`` + the five color indices and their mean errors, and the
observation/night counts.

Coordinates are converted from sexagesimal exactly as the remote VizieR provider
does (``RAh + RAm/60 + RAs/3600`` hours -> degrees; signed
``DEd + DEm/60 + DEs/3600``), so the local and remote RA/Dec agree. Blank fields
become NULL (never 0); negative declinations (including ``-00``) keep their sign.
The individual U/B/R/I magnitudes are intentionally *not* parsed here — they are
derived from V + colors at query time to match the remote provider exactly.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from .base import ParseStats, open_text, to_float, to_int

# Must match models.landolt.LandoltSource minus release_id / id / geom; extra last.
LANDOLT_COLUMNS: tuple[str, ...] = (
    "native_id",
    "ra_deg",
    "dec_deg",
    "johnson_v_mag",
    "johnson_v_err_mag",
    "b_minus_v_mag",
    "b_minus_v_err_mag",
    "u_minus_b_mag",
    "u_minus_b_err_mag",
    "v_minus_r_mag",
    "v_minus_r_err_mag",
    "r_minus_i_mag",
    "r_minus_i_err_mag",
    "v_minus_i_mag",
    "v_minus_i_err_mag",
    "n_obs",
    "n_nights",
    "extra",
)


def _s(line: str, start: int, end: int) -> str:
    """1-indexed inclusive byte slice, stripped."""
    return line[start - 1:end].strip()


class _LandoltParser:
    """Shared fixed-width Landolt parser; subclasses supply a byte map + release."""

    columns = LANDOLT_COLUMNS
    source_format = ""  # set by subclass
    release_tag = ""    # value stored in extra["release"]

    # (start, end) byte ranges, 1-indexed inclusive. Set by subclass.
    FIELDS: dict[str, tuple[int, int]] = {}
    SIGN_BYTE: int = 0  # 1-indexed position of the declination sign character
    MIN_LEN: int = 0    # shortest line that still carries a full position

    def _coords(self, line: str) -> tuple[float, float]:
        f = self.FIELDS
        rah = int(_s(line, *f["RAh"]))
        ram = int(_s(line, *f["RAm"]))
        ras = float(_s(line, *f["RAs"]))
        ded = int(_s(line, *f["DEd"]))
        dem = int(_s(line, *f["DEm"]))
        des = float(_s(line, *f["DEs"]))
        ra_deg = (rah + ram / 60.0 + ras / 3600.0) * 15.0
        sign = -1.0 if line[self.SIGN_BYTE - 1] == "-" else 1.0
        dec_deg = sign * (abs(ded) + dem / 60.0 + des / 3600.0)
        return ra_deg, dec_deg

    def iter_rows(self, paths: Iterable[Path], stats: ParseStats) -> Iterator[tuple]:
        f = self.FIELDS
        for path in paths:
            with open_text(path) as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if not line.strip():
                        continue
                    if len(line) < self.MIN_LEN:
                        stats.record_malformed(line)
                        continue
                    native_id = _s(line, *f["Name"])
                    if not native_id:
                        stats.record_malformed(line)
                        continue
                    try:
                        ra_deg, dec_deg = self._coords(line)
                    except ValueError:
                        stats.record_malformed(line)
                        continue

                    # Colors/V and their mean errors. Blank -> None (NULL); these
                    # fields have no numeric sentinel, so missing_at_or_above=None.
                    v = to_float(_s(line, *f["Vmag"]), missing_at_or_above=None)
                    bv = to_float(_s(line, *f["B-V"]), missing_at_or_above=None)
                    ub = to_float(_s(line, *f["U-B"]), missing_at_or_above=None)
                    vr = to_float(_s(line, *f["V-R"]), missing_at_or_above=None)
                    ri = to_float(_s(line, *f["R-I"]), missing_at_or_above=None)
                    vi = to_float(_s(line, *f["V-I"]), missing_at_or_above=None)
                    ev = to_float(_s(line, *f["e_Vmag"]), missing_at_or_above=None)
                    ebv = to_float(_s(line, *f["e_B-V"]), missing_at_or_above=None)
                    eub = to_float(_s(line, *f["e_U-B"]), missing_at_or_above=None)
                    evr = to_float(_s(line, *f["e_V-R"]), missing_at_or_above=None)
                    eri = to_float(_s(line, *f["e_R-I"]), missing_at_or_above=None)
                    evi = to_float(_s(line, *f["e_V-I"]), missing_at_or_above=None)
                    n_obs = to_int(_s(line, *f["Nobs"]))
                    n_nights = to_int(_s(line, *f["Nnig"]))

                    stats.parsed += 1
                    yield (
                        native_id, ra_deg, dec_deg,
                        v, ev, bv, ebv, ub, eub, vr, evr, ri, eri, vi, evi,
                        n_obs, n_nights,
                        {"release": self.release_tag},
                    )


class Landolt1992Parser(_LandoltParser):
    source_format = "landolt_1992_dat"
    release_tag = "1992"
    SIGN_BYTE = 22
    MIN_LEN = 30  # through DEs (byte 30)
    FIELDS = {
        "Name": (1, 11),
        "RAh": (13, 14), "RAm": (16, 17), "RAs": (19, 20),
        "DEd": (23, 24), "DEm": (26, 27), "DEs": (29, 30),
        "Vmag": (33, 38),
        "B-V": (40, 45), "U-B": (47, 52), "V-R": (54, 59),
        "R-I": (61, 66), "V-I": (68, 73),
        "Nobs": (75, 77), "Nnig": (79, 81),
        "e_Vmag": (84, 89), "e_B-V": (91, 96), "e_U-B": (98, 103),
        "e_V-R": (105, 110), "e_R-I": (112, 117), "e_V-I": (119, 124),
    }


class Landolt2009Parser(_LandoltParser):
    source_format = "landolt_2009_dat"
    release_tag = "2009"
    SIGN_BYTE = 26
    MIN_LEN = 37  # through DEs (byte 37)
    FIELDS = {
        "Name": (1, 11),
        "RAh": (13, 14), "RAm": (16, 17), "RAs": (19, 24),
        "DEd": (27, 28), "DEm": (30, 31), "DEs": (33, 37),
        "Vmag": (39, 44),
        "B-V": (46, 51), "U-B": (53, 58), "V-R": (60, 65),
        "R-I": (67, 72), "V-I": (74, 79),
        "Nobs": (81, 83), "Nnig": (85, 87),
        "e_Vmag": (89, 94), "e_B-V": (96, 101), "e_U-B": (103, 108),
        "e_V-R": (110, 115), "e_R-I": (117, 122), "e_V-I": (124, 129),
    }
