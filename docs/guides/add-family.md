# Adding a catalog family

The README's [checklist](../../README.md#adding-a-catalog-family) names the six
touch points. This page is the worked version: what each file must actually
contain, what the ingestion engine expects of it, and what a reviewer will look
for. It is written to be read once, front to back, before you write the first
line.

The example throughout is **Tycho-2** — an astrometric catalog with ~2.5M
sources, `BT`/`VT` magnitudes and proper motions. It is not shipped; it is a
plausible next family whose shape differs from every family that is.

## Before you start

Two decisions come first, and both are hard to reverse:

**Is this a new family, or a release of an existing one?** APASS DR6 and DR10 are
two *releases* of one family because they describe the same sources with the
same column meanings. Landolt 1992 and 2009 likewise. A new family is warranted
when the columns differ scientifically — Tycho-2 has proper motions and Tycho
photometry, which no shipped family has. Getting this wrong is expensive: a
release is a partition, a family is a table.

**What is `native_id`?** The catalog's own identifier, as text, exactly as the
upstream provider exposes it — because that is what a user will paste into
`skycat lookup`. Tycho-2's is the `TYC1-TYC2-TYC3` designation (e.g. `1-13-1`).
It does **not** have to be unique: Stetson's `Star` id repeats across clusters,
which is correct, matches the provider, and is reported as INFO rather than
treated as corruption.

Then read [provenance.md](provenance.md) and put the source files under
`SKYCAT_DATA_ROOT` before writing code. Discovery is the first thing that will
fail, and it is much easier to debug with the real files present.

## 1. `skycat/registry/catalog_defs.py` — declare it exists

```python
FamilyDef(
    slug="tycho2",                        # CLI name: `skycat import tycho2 ...`
    display_name="Tycho-2 Astrometric Catalogue",
    provider="ESA / CDS",
    description=(
        "Astrometric reference catalogue from the Tycho star mapper "
        "(Hog+ 2000, CDS I/259), with BT/VT photometry and proper motions."
    ),
    reference_url="https://cdsarc.cds.unistra.fr/viz-bin/cat/I/259",
    catalog_type="astrometric",
    data_table="tycho2_source",           # the parent table in catalog_data
    releases=(
        ReleaseDef(
            slug="main",
            name="main",                  # the registry name; `--release main`
            version="2000",
            source_subdir="Tycho-2",      # relative to SKYCAT_DATA_ROOT
            source_format="tycho2_dat",   # the parser key, registered in step 3
            data_globs=("tyc2.dat*",),
            aux_globs=("ReadMe", "suppl_1.dat", "suppl_2.dat"),
            approx_row_count=2_539_913,
        ),
    ),
)
```

Points that are load-bearing rather than descriptive:

- **`approx_row_count` is required in practice.** `tests/test_catalog_defs.py`
  asserts every shipped release has one. It is the guard against a truncated
  download: a short read of a multi-GB catalog parses cleanly and imports
  silently, and this is the only thing that notices. Use the published size.
- **`data_globs` selects the bulk data; `aux_globs` everything else.** Only
  `data_globs` matches feed the parser, the byte total, and the manifest
  checksum — so a re-downloaded ReadMe does not invalidate an import. Choose
  globs that cannot accidentally match an auxiliary table: `table4.dat`, not
  `*.dat`.
- **`slug` is lowercase and is the CLI's name for the family.** `release()` is
  matched case-insensitively against the slug, so `--release Main` works.
- **Do not add a family before its parser exists.** `importer_available=False`
  makes `import` fail fast rather than half-run, and
  `test_no_speculative_families_remain` asserts nothing speculative is sitting
  in the list.

## 2. `skycat/models/tycho2.py` — the typed table

```python
TYCHO2_ID_SEQUENCE = "tycho2_source_id_seq"


class Tycho2Source(CatalogBase):
    """A single Tycho-2 astrometric source (release-partitioned)."""

    __tablename__ = "tycho2_source"
    __table_args__ = {
        "schema": SCHEMA_DATA,
        "postgresql_partition_by": "LIST (release_id)",
    }

    release_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    id: Mapped[int] = mapped_column(
        BigInteger, Sequence(TYCHO2_ID_SEQUENCE, schema=SCHEMA_DATA), primary_key=True
    )

    native_id: Mapped[str] = mapped_column(String(24), nullable=False)  # TYC1-TYC2-TYC3
    ra_deg: Mapped[float] = mapped_column(Double, nullable=False)
    dec_deg: Mapped[float] = mapped_column(Double, nullable=False)

    tycho_bt_mag: Mapped[float | None] = mapped_column(Double)
    tycho_bt_err_mag: Mapped[float | None] = mapped_column(Double)
    tycho_vt_mag: Mapped[float | None] = mapped_column(Double)
    tycho_vt_err_mag: Mapped[float | None] = mapped_column(Double)

    pm_ra_mas_yr: Mapped[float | None] = mapped_column(Double)
    pm_dec_mas_yr: Mapped[float | None] = mapped_column(Double)
    ra_err_mas: Mapped[float | None] = mapped_column(Double)
    dec_err_mas: Mapped[float | None] = mapped_column(Double)

    extra: Mapped[dict | None] = mapped_column(JSONB)

    geom = geom_column()
```

Requirements, not suggestions:

- **`geom` comes from `models/mixins.geom_column()`.** Never retype the
  expression. It is the one thing that must be byte-identical across families,
  because the whole query layer assumes it.
- **`native_id`, `ra_deg`, `dec_deg` are declared explicitly**, and there are
  deliberately no helpers for them — their types genuinely differ per family
  (APASS `String(64)`, VSX `String(32)`). The comment in `mixins.py` explains
  why the helpers that once existed were removed.
- **The composite primary key is `(release_id, id)`** and the partition key is
  `LIST (release_id)`. PostgreSQL requires the partition key in the primary key;
  this is not a style choice.
- **Column names carry units.** `pm_ra_mas_yr`, `ra_err_mas`, `tycho_vt_mag`.
  A reader should never have to open the model to learn whether an error column
  is arcsec or mas.
- **Missing stays `NULL`.** Never `0`, never the source's sentinel. Tycho-2 has
  blank fields; APASS writes `99.999`. Both become `NULL`.
- **Per-release oddities go in `extra` (JSONB), not new columns.** A flag that
  only one release carries is exactly what `extra` is for. A column that every
  release carries and that anyone will filter or sort on should be typed.

Register the class in `skycat/models/__init__.py` — importing that module is
what attaches the table to `CatalogBase.metadata`, and Alembic, the partition
helpers, and `copy_loader.data_columns()` all read it from there.

## 3. `skycat/ingestion/parsers/tycho2.py` — the streaming parser

```python
TYCHO2_COLUMNS: tuple[str, ...] = (
    "native_id", "ra_deg", "dec_deg",
    "tycho_bt_mag", "tycho_bt_err_mag", "tycho_vt_mag", "tycho_vt_err_mag",
    "pm_ra_mas_yr", "pm_dec_mas_yr", "ra_err_mas", "dec_err_mas", "extra",
)


class Tycho2Parser:
    columns = TYCHO2_COLUMNS
    source_format = "tycho2_dat"

    def iter_rows(self, paths: Iterable[Path], stats: ParseStats) -> Iterator[tuple]:
        for path in paths:
            with open_text(path) as fh:
                for line in fh:
                    ...
                    stats.parsed += 1
                    yield (native_id, ra, dec, bt, bt_err, vt, vt_err,
                           pm_ra, pm_dec, ra_err, dec_err, extra or None)
```

The contract the engine relies on:

- **`columns` must match the model's data columns and their order.** "Data
  columns" means everything except `release_id`, `id`, and `geom` — see
  `copy_loader.EXCLUDED_FROM_STAGING`. `extra` goes last. A mismatch here
  produces a `COPY` that fails on a type error several million rows in, or
  worse, one that succeeds with shifted columns.
- **It must stream.** One tuple at a time, across files, with bounded memory.
  `iter_rows` is a generator and the loader consumes it row by row into `COPY`;
  building a list of 128M tuples is not an optimisation to defer, it is an
  out-of-memory crash on the largest family.
- **Nothing is silently dropped.** A line that cannot be parsed at all goes to
  `stats.record_malformed(line)` (which counts it and keeps the first 20 for the
  ingestion run's `detail.malformed_examples`) and is skipped. A line that
  parses but is invalid — out-of-range coordinates, a null id — should be
  yielded and left for the staging validators, which mark it with a
  `reject_reason` and retain it in a `catalog_staging.*_rejects` table.
- **Use `base.to_float` / `base.to_int` for numeric fields.** They return `None`
  for blanks and for sentinel values at or above a threshold
  (`missing_at_or_above`, defaulting to `90.0` for APASS's `99.999`). Pass
  `missing_at_or_above=None` where a real magnitude can legitimately exceed the
  threshold, as VSX does.
- **Use `base.open_text`.** It handles `.gz` transparently and reads latin-1,
  which CDS files require.
- **`gzip` support is free, encoding surprises are not.** Fixed-width CDS
  formats are byte-indexed; a UTF-8 decode of a latin-1 file shifts every column
  after the first accented character.

Register the class in `parsers/__init__.py`'s `_PARSERS` map, keyed by
`source_format`. That key is the only link between `catalog_defs.py` and the
parser, and there is no auto-discovery — this is deliberate.

## 4. `skycat/migrations/versions/000N_tycho2.py` — the parent table

Copy the shape of `0003_vsx.py`. It is raw `op.execute` DDL rather than
`op.create_table`, because Alembic's table builder cannot express `PARTITION BY
LIST` or a `GENERATED ALWAYS ... STORED` geography column.

```python
revision: str = "0007"
down_revision: Union[str, None] = "0006"      # linear chain; never a second head

def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS catalog_data.tycho2_source_id_seq")
    op.execute(f"""
        CREATE TABLE catalog_data.tycho2_source (
            release_id  integer NOT NULL,
            id          bigint  NOT NULL DEFAULT nextval('catalog_data.tycho2_source_id_seq'),
            native_id   varchar(24) NOT NULL,
            ...
            geom geography(Point,4326) GENERATED ALWAYS AS ({GEOM_EXPR}) STORED,
            PRIMARY KEY (release_id, id)
        ) PARTITION BY LIST (release_id)
    """)
    op.execute("ALTER SEQUENCE catalog_data.tycho2_source_id_seq "
               "OWNED BY catalog_data.tycho2_source.id")
    op.execute("CREATE INDEX ix_tycho2_source_geom ON catalog_data.tycho2_source USING gist (geom)")
    op.execute("CREATE INDEX ix_tycho2_source_native_id ON catalog_data.tycho2_source (native_id)")
```

Requirements:

- **One head, always.** `down_revision` is the current head and nothing else.
  `tests/test_migration_graph.py` fails the build on a fork, a broken import, or
  a duplicate revision id — with no database needed.
- **The migration creates the *parent* only.** Partitions are created by the
  ingestion runner, one per release, at import time. A migration that creates a
  partition is a migration that has to know release ids, which do not exist yet.
- **Indexes go on the parent.** The runner replicates every non-primary-key
  parent index onto the detached replacement table before `ATTACH`, so `ATTACH`
  adopts them without a rebuild scan. An index you forget here is an index no
  partition ever gets.
- **Reuse the geom expression.** Take it from `skycat/spatial`'s
  `GEOM_GENERATED_EXPR`, or copy the `GEOM_EXPR` constant verbatim from a
  sibling migration. Retyping the `CASE WHEN ra_deg > 180` mapping is how two
  families end up with subtly different spatial semantics.
- **Never put catalog data in a migration.** Data arrives through ingestion.
- **Touch only your own table.** A migration that alters another family's table
  turns a family addition into a cross-family break.

Verify without a database:

```bash
uv run pytest tests/test_migration_graph.py -q
uv run skycat migrate-status          # needs a database; shows current vs head
```

## 5. `skycat/validation/tycho2.py` — family checks *(optional)*

The catalog-independent checks come free from `validation/common.py`: coordinate
ranges, null identifiers, row counts against `approx_row_count`, spatial-index
presence. A family with no validator simply gets those.

Add a validator for the claims only this catalog can make about itself:

```python
def validate_tycho2_staging(conn: Connection, staging_fqn: str) -> list[Check]:
    checks: list[Check] = []
    implausible = int(conn.execute(text(
        f"SELECT count(*) FROM {staging_fqn} "
        f"WHERE tycho_vt_mag IS NOT NULL AND (tycho_vt_mag < -2 OR tycho_vt_mag > 16)"
    )).scalar() or 0)
    checks.append(Check("tycho2_vt_range", WARNING, implausible == 0,
                        f"{implausible} rows with implausible VT"))
    return checks
```

Register it in `validation/__init__.py`'s `_FAMILY_VALIDATORS`, keyed by family
slug.

Choosing a level is the whole design of a check:

| Level | Effect | Use for |
|---|---|---|
| `CRITICAL` | Import **fails**; the release never reaches READY | Corruption. Duplicate identifiers where the provider guarantees uniqueness; coordinates that cannot be real. |
| `WARNING` | Import completes; auto-activation is refused without `--allow-warnings` | "This looks wrong and a human should decide." Implausible magnitudes, a short row count. |
| `INFO` | Recorded only | Facts worth having in the record. How many rows carry a variable type; expected duplicate ids. |

The bar for `CRITICAL` is high: it stops a multi-hour import. If the answer to
"would you rather have this data than nothing?" is yes, it is a `WARNING`.

## 6. Tests, fixtures, and the README table

- **Commit a small sample** under `tests/data/` — six or so representative rows,
  including at least one with missing values and one that should be rejected.
  `.gitignore` denies `tests/data/*.dat` with an explicit allow-list, so add
  `!tests/data/tycho2_sample.dat` alongside your file or it will not be
  committed.
- **Add the family to `tests/conftest.py`**: copy the sample into
  `fixture_data_root` under its `source_subdir`, and add the `(family, release)`
  pair to the `imported` fixture's loop. The existing spatial, cone, crossmatch,
  ordering, and quality-filter tests then cover the new family automatically —
  that coverage is the reason the fixture is shaped as a loop.
- **Add a row to the family table at the top of `README.md`** and to
  `docs/reference/architecture.md`. `tests/test_docs.py` will not catch a
  missing row, but a user reading the README to find out what Skycat supports
  will.
- **Run the integration suite for real.** A unit run proves nothing here:

  ```bash
  # against a throwaway PostGIS — see README "Testing"
  uv run pytest tests -q --require-postgis
  ```

## What you do not have to touch

Nothing in the generic engine changes. Discovery, checksums, the staging `COPY`,
the reject-retention tables, the detached partition build, the `ATTACH` swap, the
registry lifecycle, and the health checks are all family-agnostic and read the
`FamilyDef`.

The **query layer is also automatic**, with one condition. `cone`, `lookup`, and
`crossmatch` work on any family as soon as its table exists, and `--mag-band`,
`--mag-min/--mag-max`, and `--order-by` resolve their column against the
family's table at query time — so `--order-by tycho_vt_mag` works with no
registration anywhere. The condition is that the column must exist on the table
and be numeric; anything you leave in `extra` is not orderable or filterable
this way. That is the practical cost of the JSONB escape hatch, and the reason
to type a column that anyone will actually query.

## Review checklist

- [ ] `approx_row_count` set from the published catalog size.
- [ ] `data_globs` cannot match an auxiliary table.
- [ ] `geom` from `geom_column()`; the migration reuses the shared expression.
- [ ] Parser `columns` match the model's data columns, in order, `extra` last.
- [ ] `iter_rows` is a generator; nothing accumulates across rows.
- [ ] Malformed lines counted, invalid rows left to the validators.
- [ ] Units in every column name; missing values are `NULL`.
- [ ] Migration adds one revision on the current head and touches one table.
- [ ] GiST index on `geom` and a btree on `native_id`, both on the parent.
- [ ] Sample fixture committed *and* allow-listed in `.gitignore`.
- [ ] Family added to the `imported` fixture.
- [ ] Integration suite run with `--require-postgis`.
- [ ] README family table updated.
