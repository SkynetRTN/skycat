# Source data and release provenance

A catalog release is only as trustworthy as the answer to one question: *which
upstream files is this made of, and can I rebuild it?* Skycat records enough to
answer that, but the recording is only half of it — the source tree has to be
laid out the way discovery expects, and the checksum has to be the kind that
proves what you need it to prove.

This page covers the layout, how to mirror the upstream catalogs into it, what
the importer stamps onto a release, which checksum mode to use, and how to
verify after the fact that a database release corresponds to a specific upstream
snapshot.

## The source tree

`SKYCAT_DATA_ROOT` (default `/srv/agents/catalogs`, `/catalog-data` inside
Compose) is a **read-only** tree. No Skycat command writes to it, renames within
it, or deletes from it. Generated artifacts go to `SKYCAT_WORK_ROOT`.

One directory per release, named by that release's `source_subdir` in
`skycat/registry/catalog_defs.py`:

```
$SKYCAT_DATA_ROOT/
├── APASS-DR6/       z[pm]*_6.sum      + *.zip                     (aux)
├── APASS-DR10/      z[pm]*.txt        + *.zip                     (aux)
├── VSX/             vsx.dat           + ReadMe, refs.dat*         (aux)
├── Landolt_1992/    table2.dat        + ReadMe                    (aux)
├── Landolt_2009/    table2.dat        + ReadMe, table5.dat        (aux)
└── StetsonGlobs/    table4.dat        + ReadMe, table2/1/a*.dat   (aux)
```

The left column is `data_globs`; the right is `aux_globs`. The distinction is
not cosmetic:

- **Only `data_globs` matches are parsed**, counted in `source_size_bytes`, and
  hashed into the checksum.
- **`aux_globs` matches are inventoried but excluded from all three.** A
  re-downloaded `ReadMe` with a fresh mtime must not invalidate a 128M-row
  import, and a ReadMe that never arrived should still be visible to an operator
  staging a release.

Nested layouts are not scanned: the globs are relative to the release directory
and non-recursive. Put the data files directly in it, or point `--source-dir`
somewhere else.

## Mirroring the upstream catalogs

Skycat never downloads anything. Mirroring is a deliberate, separate step, which
is what makes a release reproducible — an importer that fetched from the network
would produce a different result on a different day.

**CDS/VizieR catalogs** (VSX, Landolt, Stetson) are served from a stable path
built from the catalog designation shown in each family's `reference_url`:

```
https://cdsarc.cds.unistra.fr/ftp/<designation>/
```

So `II/183A` (Landolt 1992) lives at `.../ftp/II/183A/`, and `J/MNRAS/485/3042`
(Stetson) at `.../ftp/J/MNRAS/485/3042/`. Mirror a directory with `wget`:

```bash
cd "$SKYCAT_DATA_ROOT/Landolt_1992"
wget --no-parent --no-host-directories --cut-dirs=3 --timestamping \
     --reject "index.html*" \
     --recursive --level=1 \
     https://cdsarc.cds.unistra.fr/ftp/II/183A/
```

`--timestamping` matters: it preserves the upstream mtime, which the default
checksum mode reads. A mirror that rewrites mtimes makes every re-import look
like a changed source.

`--cut-dirs` must equal the number of path components after the host, so it
changes with the designation's depth: `3` for `ftp/II/183A`, `5` for
`ftp/J/MNRAS/485/3042`. Get it wrong and the files land in a nested
subdirectory, where the non-recursive globs will not see them — `skycat
discover` will report `present=false` with "no data files matching".

Confirm the designation against `reference_url` in `catalog_defs.py` before
mirroring — CDS occasionally supersedes a catalog with a new designation
(`II/183` → `II/183A`), and a silently-different upstream is exactly what this
page exists to prevent.

**APASS** is distributed by AAVSO rather than CDS; DR6 and DR10 come as zipped
per-declination-band files from the APASS page linked in `catalog_defs.py`.
Unpack them into `APASS-DR6/` and `APASS-DR10/` and leave the `.zip` files in
place — they match `aux_globs`, so they are inventoried without being parsed or
hashed, which is a useful record of what was unpacked.

After mirroring, take the inventory before importing anything:

```bash
uv run skycat discover                     # every family/release
uv run skycat discover landolt 1992        # one, with issues explained
uv run skycat --json discover > inventory-$(date -I).json
```

`discover` reports, per release: `present`, `source_dir`, the data-file count
(`files`), the auxiliary count (`aux_files`), total data `bytes`, and `issues` —
a missing directory or a glob that matched nothing. It touches no database, so
it is safe to run against a production data root from anywhere.

Keep that JSON. It is the only record of what the tree looked like *before* the
import, and it is what you compare against when a release's byte count does not
match what you expected.

## What a release records

Every import stamps the `catalog_registry.catalog_release` row:

| Column | Meaning |
|---|---|
| `source_location` | Absolute path of the directory the files were read from. |
| `source_checksum` | Manifest or content hash — see below. |
| `source_size_bytes` | Total bytes of the **data** files only. |
| `source_modified_at` | Latest mtime across the data files. |
| `source_format` | The parser key, e.g. `landolt_1992_dat`. |
| `expected_row_count` | The published size from `ReleaseDef.approx_row_count`. |
| `parsed_row_count` | Rows the parser produced. |
| `imported_row_count` | Rows that reached the production partition. |
| `rejected_row_count` | Rows rejected by staging validation. |
| `importer_version` | `IMPORTER_VERSION` at import time — parser/transform semantics. |
| `internal_schema_version` | `INTERNAL_SCHEMA_VERSION` — production row shape. |
| `production_table` | The physical partition, e.g. `catalog_data.landolt_source_r4`. |
| `import_started_at` / `import_completed_at` / `validated_at` | Timing. |

The matching `ingestion_run` row adds the host it ran on, the stage it reached,
and `detail.malformed_examples` — the first 20 lines the parser could not read
at all. Those examples are frequently the fastest way to identify a source that
is a different format than expected.

The three counts are a provenance statement in themselves:
`parsed = imported + rejected` should hold, and `imported` well below
`expected_row_count` is the truncated-download signature that the row-count
validator warns on.

## Checksum modes

| | Manifest (default) | Content (`--content-checksum`) |
|---|---|---|
| Hashes | `relative_path:size_bytes:mtime` per data file | the file bytes, streamed in 1 MiB chunks |
| Cost | milliseconds | minutes to hours; reads the entire dataset |
| Detects | a file added, removed, resized, or re-dated | any change to the bytes |
| Misses | a same-size, same-mtime edit | nothing |
| False positives | a copy that did not preserve mtimes (`cp` without `-p`, `rsync` without `-t`, a container bind-mount rebuild) | none |

Both are SHA-256 over data files only, sorted by relative path, with auxiliary
files excluded.

**Use the manifest hash for routine operations.** It is what idempotency runs
on: a re-import of an unchanged release is skipped rather than repeated, and
that skip is the difference between a five-second no-op and a three-hour rebuild
of APASS DR10.

**Use `--content-checksum` when the checksum is the evidence** — the import that
establishes a release you will later have to prove something about, a mirror you
did not create yourself, or any investigation of a suspected bad source. It is
the only mode that can distinguish "the same file" from "a file with the same
name, size, and date".

The mode used is not recorded on the release row. If it matters, say so in the
release's `notes` — and prefer to standardise: content-checksum every import of
a family you care about proving, so that comparing two releases' checksums is
always meaningful.

## Proving a release matches an upstream snapshot

Neither `skycat releases` nor `skycat history` prints `source_checksum` today,
so verification is a registry query. As `catalog_reader`:

```sql
SELECT f.slug,
       r.name,
       r.state,
       r.source_location,
       r.source_checksum,
       r.source_size_bytes,
       r.source_modified_at,
       r.expected_row_count,
       r.imported_row_count,
       r.rejected_row_count,
       r.importer_version,
       r.internal_schema_version,
       r.production_table
FROM catalog_registry.catalog_release r
JOIN catalog_registry.catalog_family f ON f.id = r.family_id
WHERE f.slug = 'landolt'
ORDER BY r.name;
```

To check a candidate source tree against a recorded release without importing
it, recompute the same hash the importer would have:

```bash
uv run python - <<'PY'
from skycat.config import CatalogSettings
from skycat.ingestion.discovery import compute_content_checksum, discover_one

d = discover_one(CatalogSettings.from_env().data_root, "landolt", "1992")
print("present:", d.present, d.issues)
print("files:  ", [f.relative_path for f in d.data_files])
print("bytes:  ", d.total_bytes)
print("manifest:", d.manifest_checksum())
print("content: ", compute_content_checksum(d.data_files))
PY
```

Compare the printed value against `source_checksum`. Which one to compare
against depends on how the release was imported — this is the reason to
standardise on one mode.

A full chain of custody for "this release is upstream snapshot X" is:

1. The `--json discover` inventory taken at mirror time — file names, counts,
   sizes.
2. `source_checksum` (content mode) on the release row, recomputable from the
   tree at any time.
3. `source_size_bytes` and `source_modified_at`, which catch a re-mirror that
   changed nothing but the dates.
4. `imported_row_count` against the catalog's published size, which is the only
   check that looks at what upstream *says* it published rather than at what
   arrived.
5. `importer_version` and `internal_schema_version`, which say *how* the bytes
   were turned into rows. Two releases with the same source checksum and
   different importer versions are not necessarily the same rows.

## Rebuilding a release

Reproducibility is the point of all of the above:

```bash
# 1. Restore the source tree and confirm it matches the recorded inventory.
uv run skycat --json discover apass dr10

# 2. Re-import with the checksum mode the original used. --replace is required
#    because the release already exists; --force additionally is required if it
#    is ACTIVE. An in-place replace of the ACTIVE release stays ACTIVE and keeps
#    serving the old partition until the atomic swap.
uv run skycat import apass dr10 --replace --force --content-checksum

# 3. Compare the new row counts and checksum against the previous ones.
uv run skycat releases apass
uv run skycat history apass
```

If step 3 shows a different `imported_row_count` for an identical
`source_checksum`, the parser or transform changed — check `importer_version`
against the previous release. That is what the version is for.

## Retention

The source tree is not a backup of the database, and the database is not a
backup of the source tree. Keep both:

- **Sources** for every release you might have to rebuild or prove. They are
  read-only and unchanging, so they archive well.
- **Releases** per the policy in the README: the active release plus at most the
  previous (superseded) one for rollback. `remove-release` anything older once
  the new DR has served its soak period.

Removing a release deletes its registry row, and with it the provenance record.
If you need to retain evidence about a release you are removing, export the
registry query above first.
