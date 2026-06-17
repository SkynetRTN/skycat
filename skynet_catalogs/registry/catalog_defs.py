"""Static, code-level definitions of catalog families and their releases.

This is the single source of truth for *what* catalogs exist, where their source
data lives relative to ``SKYNET_CATALOG_DATA_ROOT``, and which parser/format
applies. Used by source discovery, the registry, and the importers.

Adding a new catalog family / release is a matter of adding an entry here plus a
parser + (if its schema differs) a data model + migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReleaseDef:
    slug: str               # e.g. "dr6"
    name: str               # human/registry name, e.g. "DR6"
    version: str            # e.g. "6"
    source_subdir: str      # relative to data root, e.g. "APASS-DR6"
    source_format: str      # parser key, e.g. "apass_dr6_sum"
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
    catalog_type: str           # "photometric", "variable_star", "astrometric", …
    data_table: str             # physical parent table in catalog_data
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
    # --- Planned families (no importer yet) ----------------------------------
    FamilyDef(
        slug="panstarrs", display_name="Pan-STARRS", provider="IfA Hawaii",
        description="Pan-STARRS multi-band survey (planned).",
        reference_url="https://panstarrs.stsci.edu/", catalog_type="photometric",
        data_table="panstarrs_source", importer_available=False,
    ),
    FamilyDef(
        slug="twomass", display_name="2MASS", provider="IPAC/UMass",
        description="Two Micron All Sky Survey (planned).",
        reference_url="https://irsa.ipac.caltech.edu/Missions/2mass.html",
        catalog_type="photometric", data_table="twomass_source", importer_available=False,
    ),
    FamilyDef(
        slug="ucac5", display_name="UCAC5", provider="USNO",
        description="USNO CCD Astrograph Catalog 5 (planned).",
        reference_url="https://www.usno.navy.mil/", catalog_type="astrometric",
        data_table="ucac5_source", importer_available=False,
    ),
    FamilyDef(
        slug="tycho2", display_name="Tycho-2", provider="ESA",
        description="Tycho-2 astrometric catalog (planned).",
        reference_url="https://www.cosmos.esa.int/web/hipparcos/tycho-2",
        catalog_type="astrometric", data_table="tycho2_source", importer_available=False,
    ),
    FamilyDef(
        slug="landolt", display_name="Landolt Standards", provider="Landolt",
        description="Landolt photometric standard stars (planned).",
        reference_url="https://doi.org/10.1088/0004-6256/137/5/4186",
        catalog_type="photometric", data_table="landolt_source", importer_available=False,
    ),
    FamilyDef(
        slug="stetson", display_name="Stetson Standards", provider="Stetson/CADC",
        description="Stetson photometric standard fields (planned).",
        reference_url="https://www.canfar.net/storage/list/STETSON",
        catalog_type="photometric", data_table="stetson_source", importer_available=False,
    ),
    FamilyDef(
        slug="skymapper", display_name="SkyMapper", provider="ANU",
        description="SkyMapper Southern Survey (planned).",
        reference_url="https://skymapper.anu.edu.au/", catalog_type="photometric",
        data_table="skymapper_source", importer_available=False,
    ),
    FamilyDef(
        slug="usnob1", display_name="USNO-B1.0", provider="USNO",
        description="USNO-B1.0 astrometric/photometric catalog (planned).",
        reference_url="https://www.usno.navy.mil/", catalog_type="astrometric",
        data_table="usnob1_source", importer_available=False,
    ),
)

FAMILY_BY_SLUG: dict[str, FamilyDef] = {f.slug: f for f in CATALOG_FAMILIES}


def get_family_def(slug: str) -> FamilyDef | None:
    return FAMILY_BY_SLUG.get(slug.lower())
