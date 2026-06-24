"""VSX (``vsx.dat``) fixed-width streaming parser.

Byte layout (1-indexed, from the CDS ReadMe) -> 0-indexed slices:

==========  =====================  ==============================
Bytes       Field                  -> column
==========  =====================  ==============================
1-8         OID                    vsx_oid / native_id
10-39       Name                   name
41          V (variability flag)   var_flag
43-51       RAdeg                  ra_deg
53-61       DEdeg                  dec_deg
63-92       Type                   var_type
94          l_max                  extra
96-102      max                    max_mag
104         u_max                  extra
106-115     n_max                  max_passband
117         f_min ('(' = ampl.)    min_is_amplitude
119         l_min                  extra
121-127     min                    min_mag / amplitude_mag
129         u_min                  extra
131-138     n_min                  min_passband
140-153     Epoch (HJD)            epoch_hjd
155         u_Epoch                extra
157         l_Period               extra
159-177     Period (days)          period_days
179         u_Period               extra
181-209     Sp                     spectral_type
==========  =====================  ==============================
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from .base import ParseStats, open_text, to_float

VSX_COLUMNS: tuple[str, ...] = (
    "native_id", "vsx_oid", "name", "ra_deg", "dec_deg", "var_flag", "var_type",
    "max_mag", "max_passband", "min_mag", "min_passband", "min_is_amplitude",
    "amplitude_mag", "period_days", "epoch_hjd", "spectral_type", "extra",
)


def _s(line: str, start: int, end: int) -> str:
    """1-indexed inclusive byte slice, stripped."""
    return line[start - 1:end].strip()


class VsxParser:
    columns = VSX_COLUMNS
    source_format = "vsx_dat"

    def iter_rows(self, paths: Iterable[Path], stats: ParseStats) -> Iterator[tuple]:
        for path in paths:
            with open_text(path) as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if len(line) < 61:  # need at least through DEdeg
                        if line.strip():
                            stats.record_malformed(line)
                        continue
                    oid_s = _s(line, 1, 8)
                    ra_s = _s(line, 43, 51)
                    dec_s = _s(line, 53, 61)
                    try:
                        vsx_oid = int(oid_s)
                        ra = float(ra_s)
                        dec = float(dec_s)
                    except ValueError:
                        stats.record_malformed(line)
                        continue

                    name = _s(line, 10, 39) or None
                    var_flag_s = _s(line, 41, 41)
                    var_flag = int(var_flag_s) if var_flag_s.isdigit() else None
                    var_type = _s(line, 63, 92) or None
                    l_max = _s(line, 94, 94) or None
                    max_mag = to_float(_s(line, 96, 102), missing_at_or_above=None)
                    u_max = _s(line, 104, 104) or None
                    max_passband = _s(line, 106, 115) or None
                    f_min = _s(line, 117, 117) or None
                    l_min = _s(line, 119, 119) or None
                    min_val = to_float(_s(line, 121, 127), missing_at_or_above=None)
                    u_min = _s(line, 129, 129) or None
                    min_passband = _s(line, 131, 138) or None
                    epoch = to_float(_s(line, 140, 153), missing_at_or_above=None)
                    u_epoch = _s(line, 155, 155) or None
                    l_period = _s(line, 157, 157) or None
                    period = to_float(_s(line, 159, 177), missing_at_or_above=None)
                    u_period = _s(line, 179, 179) or None
                    sp = _s(line, 181, 209) or None

                    min_is_amplitude = f_min == "("
                    amplitude_mag = min_val if min_is_amplitude else None
                    min_mag = None if min_is_amplitude else min_val

                    extra = {
                        "l_max": l_max, "u_max": u_max, "f_min": f_min,
                        "l_min": l_min, "u_min": u_min, "u_epoch": u_epoch,
                        "l_period": l_period, "u_period": u_period,
                    }
                    extra = {k: v for k, v in extra.items() if v is not None}

                    stats.parsed += 1
                    yield (
                        str(vsx_oid), vsx_oid, name, ra, dec, var_flag, var_type,
                        max_mag, max_passband, min_mag, min_passband, min_is_amplitude,
                        amplitude_mag, period, epoch, sp, extra or None,
                    )
