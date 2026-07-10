"""Static, code-level definitions of catalog families and their releases.

This is the single source of truth for *what* catalogs exist, where their source
data lives relative to ``SKYCAT_DATA_ROOT``, and which parser/format
applies. Used by source discovery, the registry, and the importers.

Adding a new catalog family / release is a matter of adding an entry here plus a
parser + (if its schema differs) a data model + migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReleaseDef:
    slug: str  # e.g. "dr6"
    name: str  # human/registry name, e.g. "DR6"
    version: str  # e.g. "6"
    source_subdir: str  # relative to data root, e.g. "APASS-DR6"
    source_format: str  # parser key, e.g. "apass_dr6_sum"
    # Glob patterns (relative to source_subdir) for the actual data files.
    data_globs: tuple[str, ...] = ()
    # Optional files that are part of the release but not bulk data (readmes…).
    aux_globs: tuple[str, ...] = ()


@dataclass(frozen=True)
class FamilyDef:
    slug: str
    display_name: str
    provider: str
    description: str
    reference_url: str
    catalog_type: str  # "photometric", "variable_star", "astrometric", …
    data_table: str  # physical parent table in catalog_data
    coordinate_frame: str = "ICRS"
    epoch_jyear: float | None = 2000.0
    releases: tuple[ReleaseDef, ...] = field(default_factory=tuple)
    # Families with no importer yet are listed for completeness / planning.
    importer_available: bool = True

    def release(self, slug: str) -> ReleaseDef | None:
        for rel in self.releases:
            if rel.slug == slug.lower():
                return rel
        return None


CATALOG_FAMILIES: tuple[FamilyDef, ...] = (
    FamilyDef(
        slug="apass",
        display_name="AAVSO Photometric All-Sky Survey",
        provider="AAVSO",
        description=(
            "All-sky photometric survey (Johnson B/V + Sloan u'g'r'i'z' and Y). "
            "DR6 and DR10 are modelled as releases of one APASS family."
        ),
        reference_url="https://www.aavso.org/apass",
        catalog_type="photometric",
        data_table="apass_source",
        releases=(
            ReleaseDef(
                slug="dr6",
                name="DR6",
                version="6",
                source_subdir="APASS-DR6",
                source_format="apass_dr6_sum",
                data_globs=("z[pm]*_6.sum",),
                aux_globs=("*.zip",),
            ),
            ReleaseDef(
                slug="dr10",
                name="DR10",
                version="10",
                source_subdir="APASS-DR10",
                source_format="apass_dr10_txt",
                data_globs=("z[pm]*.txt",),
                aux_globs=("*.zip",),
            ),
        ),
    ),
    FamilyDef(
        slug="vsx",
        display_name="AAVSO International Variable Star Index",
        provider="AAVSO / CDS",
        description="Variable-star index (CDS B/vsx). Fixed-width vsx.dat.",
        reference_url="https://www.aavso.org/vsx/",
        catalog_type="variable_star",
        data_table="vsx_source",
        releases=(
            ReleaseDef(
                slug="current",
                name="current",
                version="current",
                source_subdir="VSX",
                source_format="vsx_dat",
                data_globs=("vsx.dat",),
                aux_globs=("ReadMe", "refs.dat*"),
            ),
        ),
    ),
    FamilyDef(
        slug="landolt",
        display_name="Landolt UBVRI Photometric Standards",
        provider="Landolt / CDS",
        description=(
            "Landolt UBVRI photoelectric standard stars on the Johnson-Kron-Cousins "
            "system, centred on the celestial equator. Modelled as one family with "
            "two releases: 1992 (CDS II/183A) and 2009 (CDS J/AJ/137/4186). Each "
            "release stores V plus the five color indices (B-V, U-B, V-R, R-I, V-I) "
            "and their errors; the individual U/B/R/I bands are derived at query time "
            "exactly as the remote VizieR provider does."
        ),
        reference_url="https://cdsarc.cds.unistra.fr/viz-bin/cat/II/183A",
        catalog_type="photometric",
        data_table="landolt_source",
        releases=(
            ReleaseDef(
                slug="1992",
                name="1992",
                version="1992",
                source_subdir="Landolt_1992",
                source_format="landolt_1992_dat",
                # table2.dat = the 526 standard stars (the only photometry table).
                data_globs=("table2.dat",),
                aux_globs=("ReadMe",),
            ),
            ReleaseDef(
                slug="2009",
                name="2009",
                version="2009",
                source_subdir="Landolt_2009",
                source_format="landolt_2009_dat",
                # table2.dat = UBVRI photometry; table5.dat (coords + proper motions,
                # UCAC2/2MASS ids) is auxiliary and not part of the remote row contract.
                data_globs=("table2.dat",),
                aux_globs=("ReadMe", "table5.dat"),
            ),
        ),
    ),
    FamilyDef(
        slug="stetson",
        display_name="Stetson UBVRI Photometry in Globular Clusters",
        provider="Stetson / CDS",
        description=(
            "Homogeneous Johnson-Cousins UBVRI photometry for 48 Galactic globular "
            "clusters (Stetson+ 2019, CDS J/MNRAS/485/3042). The StetsonGlobs release "
            "imports the ~4.9M-row final catalogue (table4.dat); the per-cluster Star "
            "id is unique only within a cluster, matching the remote provider."
        ),
        reference_url="https://cdsarc.cds.unistra.fr/viz-bin/cat/J/MNRAS/485/3042",
        catalog_type="photometric",
        data_table="stetson_source",
        epoch_jyear=2000.0,
        releases=(
            ReleaseDef(
                slug="stetsonglobs",
                name="StetsonGlobs",
                version="2019",
                source_subdir="StetsonGlobs",
                source_format="stetson_globs_dat",
                # table4.dat = the final per-star catalogue. table2.dat (cluster list)
                # and table1/tablea* are auxiliary observation metadata.
                data_globs=("table4.dat",),
                aux_globs=("ReadMe", "table2.dat", "table1.dat", "tablea*.dat"),
            ),
        ),
    ),
)

FAMILY_BY_SLUG: dict[str, FamilyDef] = {f.slug: f for f in CATALOG_FAMILIES}


def get_family_def(slug: str) -> FamilyDef | None:
    return FAMILY_BY_SLUG.get(slug.lower())
