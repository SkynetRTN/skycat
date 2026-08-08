# API stability

Skycat has more public surface than a Python package usually does. Downstream
code depends on the import path *and* on the CLI's exit codes, the environment
variable names, the JSON shapes, the SQL schema, and the registry's state
vocabulary. This page says which of those are contracts and which are internals
that may change in any release.

Skycat is `0.x`. Nothing here is a promise of permanence — it is a promise about
**how a change is made**: a break in a stable surface is called out in the
release notes and the changelog, with a migration note. A change to an internal
surface is not. See [release.md](../operations/release.md) for how versions are cut.

## Stable

Depend on these. Breaking them is a release-note event.

### Python

`skycat.__all__` is the stable top-level export set. A name in it keeps its
meaning; names are added, not repurposed. Anything reached by a deeper import
path is covered by the table below or by *Internal*.

| Surface | Contract |
|---|---|
| `from skycat import CatalogReader` | The supported read path. Constructor keywords, `from_env()`, `cone()`, `crossmatch()`, `lookup()`, `active_release()`, `invalidate()`, `close()`, context-manager use, and the meaning of each keyword argument. |
| `skycat.__version__` | The package version, matching `pyproject.toml`. |
| Query row dicts | Rows come back as `dict`. Documented keys keep their names, units, and meaning. **New keys may be added** — index by key, never by position, and do not assume the key set is closed. |
| `skycat.constants` | `CatalogReleaseState`, `IngestionRunStatus`, `ValidationStatus`, `CatalogRole`, the schema names, `SRID`, and `POSTGIS_SPHERE_RADIUS_M`. New members may be added to the enums. |
| `skycat.config.CatalogSettings` | `from_env()`, `config_for(role)`, and the field names that mirror the environment variables. |
| Exception types | `CatalogConfigError`, `CatalogQueryError`, `IngestionError`, `ReleaseStateError` keep their names, module paths, and the situations that raise them. |

`cone_search()`, `lookup_native_id()`, and `batch_crossmatch()` in `skycat.query`
remain importable and keep their signatures, but `CatalogReader` is the
supported entry point: it is the only one that owns pooling, release caching,
and a statement timeout. Code that calls the bare functions is responsible for
all three.

### CLI

The `skycat` console script is a contract, because Kubernetes Jobs and shell
operators are both callers that cannot be refactored by a search-and-replace.

- **Command names and their arguments** — the table in the
  [README](../../README.md#cli).
- **Flag names.** A flag may gain a new value; it does not silently change
  meaning.
- **Exit codes**, which the ingest Job reads:

  | Code | Meaning |
  |---|---|
  | `0` | The command did what it said. An `--activate` that exits 0 *did* activate. |
  | `1` | Operational failure: `CatalogQueryError`, `IngestionError`, `ReleaseStateError`. Includes "imported but refused to activate" (validation warnings without `--allow-warnings`). |
  | `2` | Configuration failure: `CatalogConfigError` — bad or missing `SKYCAT_DB_*`, a reserved database name, a production-like target without an override. |

- **`--json` output.** Every command accepts the group-level `--json`. Documented
  keys keep their names and types; new keys may be added. Parse defensively.

### Configuration

Every `SKYCAT_DB_*` and `SKYCAT_*` variable in the README's configuration table,
its default, and its precedence. Adding a variable is not a break; renaming or
repurposing one is.

### Database

The catalog database is a contract because other processes read it directly —
a reporting query, a dashboard, a `psql` session during an incident.

| Surface | Contract |
|---|---|
| Schema names | `catalog_registry`, `catalog_data`, `catalog_staging`. |
| Role names | `catalog_owner`, `catalog_ingest`, `catalog_reader`, and their grants. |
| Registry tables | `catalog_family`, `catalog_release`, `ingestion_run`, `validation_summary` — column names and meanings. Columns may be added. |
| Release-state vocabulary | The six values of `CatalogReleaseState`, and the rule that at most one release per family is `active`. |
| Family data tables | The per-family parent table name (`apass_source`, `vsx_source`, `landolt_source`, `stetson_source`), `LIST (release_id)` partitioning, and the partition naming `<parent>_r<release_id>`. |
| Column semantics | Unit-suffixed column names (`johnson_v_mag`, `ra_err_arcsec`), and that a missing value is `NULL` — never `0`, never a sentinel like `99.999`. |
| `geom` | `geography(Point,4326)`, `GENERATED ALWAYS ... STORED`, with the RA→longitude mapping in `skycat/constants.py`. Spherical distance (`use_spheroid => false`). |

Reading these tables as `catalog_reader` is supported. Writing to them by any
route other than the `skycat` CLI or the ingestion API is not — the release
lifecycle has invariants (the partial unique index, partition/registry
agreement) that a manual `UPDATE` will not maintain.

## Internal

These may change in any release, without a note. If your code imports one,
expect to fix it.

- **Everything under `skycat.ingestion`** except the `import_release()` entry
  point and the `ImportReport` field names. The runner's phase structure,
  staging table naming, transform SQL, and index replication are implementation.
- **`skycat.validation`** — check names, levels, and the `checks` list shape
  inside a `ValidationSummary`. Read them for diagnostics; do not branch on
  them.
- **`skycat.ingestion.parsers`** — parser classes, the `source_format` keys, and
  `ParseStats`. The keys are data-layout identifiers, not an API.
- **`skycat.database`** — `engine.py`, `init.py`, `roles.py`, `migrate.py`,
  `orm.py`, `postgis.py`. Use the CLI, not these.
- **`skycat.models`** — the SQLAlchemy classes themselves, as Python objects.
  The *tables* they describe are stable (above); the mapped classes,
  relationships, and mixins are not, and the ORM layer is not a supported query
  interface for other applications.
- **`skycat.health`** — `HealthReport` check names and the `--json` check list.
  New checks appear as coverage improves; a check may be renamed or split.
- **`skycat.registry.catalog_defs`** — `FamilyDef` / `ReleaseDef` fields. This
  is where new families are added, so it changes by design.
- **Alembic revision identifiers.** The migration graph is linear and its
  identifiers are stable once released, but they are not something to branch on.
  Ask `skycat migrate-status`.
- **`catalog_staging` contents**, including the retained `*_rejects` tables.
  Diagnostics, kept for humans, not an interchange format.

## Adding a catalog family

Adding a family is an additive change to a stable surface: a new parent table,
new columns, a new `source_format` key, a new row in the family table. It does
not break existing families, and it does not need a major version. The migration
that creates it must never touch another family's table — see
[add-family.md](../guides/add-family.md).

## What "stable" does not cover

- **Row ordering** without an explicit `order_by`. The default is nearest-first
  by angular separation; a query with `limit` and no `order_by` is
  under-specified by the caller, not by Skycat.
- **Performance.** Target latencies live in [performance.md](../operations/performance.md) and
  are goals for tuning, not a contract.
- **`approx_row_count`** values. They track upstream catalog sizes and change
  when upstream does.
- **Anything in `docs/working/`.** Dated planning notes; excluded from the
  doc tests for exactly this reason.
