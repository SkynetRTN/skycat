---
status: open
reviewed: 2026-08-09
implementation: not-started
document-type: architecture action plan
inputs:
  - docs/working/remote-catalogs.md
  - skycat/client.py
  - skycat/query/cone.py
  - skycat/query/crossmatch.py
  - skycat/registry/catalog_defs.py
---

# Remote query architecture action plan

This plan turns the remote service survey in
[remote-catalogs.md](remote-catalogs.md) into an implementation structure for
`CatalogRemoteReader` and the surrounding remote-query code. It is intentionally
adjacent to, not layered inside, the current local PostgreSQL/PostGIS reader.

## 1. Architectural position

`CatalogReader` remains the local-only, release-aware PostgreSQL/PostGIS reader.
No remote fallback, mode flag, backend selector, or VizieR/SIMBAD dependency
should be added to it.

Add a separate remote package and facade:

- Public facade: `CatalogRemoteReader`.
- First import path: `skycat.remote.CatalogRemoteReader`.
- Top-level export from `skycat.__all__`: defer until the surface is ready to be
  documented as stable under `docs/reference/api-stability.md`.
- Remote dependencies are normal project dependencies once a backend needs them.
  Do not add a `skycat[remote]` optional extra; if `astroquery`, `astropy`,
  `numpy`, or `pyvo` are required for supported remote behavior, declare them in
  the main dependency set with an explicit supported version range.

The name `CatalogRemoteReader` is the name used by this plan. The survey used
`RemoteCatalogReader` as a working name; implementation should settle on one
name before any public docs or exports land.

## 2. Non-goals

- Do not add local-first or remote-fallback behavior to Skycat. Routing between
  local and remote providers belongs to downstream consumers.
- Do not make remote rows pretend to be local release rows. Remote results carry
  remote origin, not `release_id`, `release_name`, or local registry state.
- Do not implement remote batch crossmatch by looping over inputs and issuing
  one HTTP request per input. Until VizieR TAP upload joins are proven and
  designed, remote crossmatch is unsupported.
- Do not port the legacy VizieR plugins mechanically. The declaration data is
  useful; the runtime patterns are not.
- Do not put FITS `FILTER` to reference-band selection in Skycat remote code.
  Those transformations are consumer calibration policy.

## 3. Proposed module boundaries

Create a new `skycat/remote/` package. Keep `skycat/query/` scoped to local SQL
query services unless a later refactor extracts pure helpers that both packages
use.

| Module | Responsibility |
|---|---|
| `skycat/remote/__init__.py` | Re-export the experimental remote public surface from the remote package only. |
| `skycat/remote/client.py` | `CatalogRemoteReader`: facade, lazy backend lifecycle, provider lookup, query orchestration, context-manager support. |
| `skycat/remote/config.py` | Remote settings from environment and constructor overrides. Use a distinct prefix such as `SKYCAT_REMOTE_QUERY_*`. |
| `skycat/remote/errors.py` | Remote exception taxonomy. Do not subclass `CatalogQueryError` for service failures. |
| `skycat/remote/results.py` | Typed result dataclasses and JSON/dict serialization helpers. |
| `skycat/remote/definitions.py` | Validated dataclasses for remote provider definitions, band mappings, coordinate mappings, identifiers, filters, and capabilities. |
| `skycat/remote/registry.py` | Static predefined remote providers plus lookup/listing helpers. This is independent of `skycat.registry.catalog_defs`. |
| `skycat/remote/filters.py` | Safe remote filter model and translation helpers. No raw service filter strings from untrusted caller input. |
| `skycat/remote/expressions.py` | Small allow-listed expression system for row normalization. No per-row `eval`. |
| `skycat/remote/backends/base.py` | Backend protocol and capability model. |
| `skycat/remote/backends/vizier.py` | VizieR adapter: query construction, astroquery import, timeout/cache/server handling, Astropy table normalization. |
| `skycat/remote/backends/simbad.py` | SIMBAD name resolver adapter. Separate result type from catalog rows. |
| `skycat/remote/backends/tap.py` | Later shared TAP/ADQL utilities if VizieR TAP, SIMBAD TAP, or NED TAP need common code. |
| `skycat/remote/cache.py` | Optional exact-key response cache if needed. No coordinate quantization. |

Keep local `skycat/client.py`, `skycat/query/cone.py`,
`skycat/query/crossmatch.py`, and `skycat/registry/catalog_defs.py` unchanged in
the first implementation phase except for any explicitly reviewed pure-helper
extraction.

## 4. Provider definition model

Remote catalog definitions are data, but they must be typed and validated before
any network call. Use frozen dataclasses plus explicit validation functions
instead of untyped dicts or dynamic classes.

Minimum definition shape:

```python
RemoteCatalogDef(
    key="apass",
    display_name="APASS via VizieR",
    service="vizier",
    upstream_id="II/336/apass9",
    provider_version="APASS9 / DR9",
    role="photometric",
    row_limit=1000,
    default_order="johnson_v_mag",
    coordinates=CoordinateMapping(
        ra=ColumnMapping("RAJ2000", unit="deg"),
        dec=ColumnMapping("DEJ2000", unit="deg"),
        frame="ICRS",
        epoch_jyear=None,
    ),
    native_id=IdentifierMapping(template="{recno}", required_columns=("recno",)),
    magnitudes=(...),
    extra_columns=(...),
    filters=(...),
    capabilities=RemoteCapabilities(cone=True, lookup=True, crossmatch=False),
)
```

Required validation:

- `key` is lowercase, stable, and unique in the remote registry.
- `service` is a known backend.
- `upstream_id` is present and included in every result origin.
- `row_limit` is positive and bounded.
- Coordinate mappings declare units and required columns.
- Identifier mapping always produces `str`.
- Magnitude mappings declare band key, magnitude column or expression, optional
  error column or expression, unit, and sentinel/null rules.
- Filterable fields are allow-listed per provider.
- Sortable fields are allow-listed per provider.
- Required columns are derived from mappings and filters, not from `SELECT *`.
- Provider role marks calibration suitability. `vsx` should be metadata or
  exclusion, not a calibration source.

Provider keys can stay convenient (`apass`, `landolt`, `stetson`) because they
are scoped to the remote reader, but results and provider listings must expose
the true remote identity: service, upstream designation, server/mirror, and
provider version label.

## 5. Expression and mapping rules

The legacy provider contract used Python expression strings and per-row `eval`.
Keep the declarative intent, replace the execution model.

Supported mapping primitives should be deliberately small:

- Direct column reference.
- Numeric arithmetic over column references and constants: add, subtract,
  multiply, divide.
- Sentinel handling: values such as `99`, `99.99`, blank, masked, or NaN become
  `None`.
- Sexagesimal RA/Dec parsing for known VizieR tables.
- String templates for identifiers, such as formatted Tycho IDs.
- Optional provider-specific normalizer functions only when the operation cannot
  be represented safely in the expression model; these should live beside the
  provider definition and have focused tests.

Do not include broad calibration transformations such as FITS filter aliases,
Jester/Jordi, Tonry, Lupton, or 2MASS color polynomials in the remote provider
core. If a consumer needs those, it can apply them after receiving measured
remote values.

Rows with no surviving magnitudes must still be returned when they have valid
identity and coordinates. Missing photometry is data, not a reason to drop the
source.

## 6. Result model

Use typed result dataclasses internally and publicly within `skycat.remote`.
Provide `to_dict()` for CLI JSON and service integration.

Catalog row shape:

```python
RemoteCatalogRow(
    provider_key="apass",
    native_id="123456",
    ra_deg=10.123,
    dec_deg=-2.5,
    separation_deg=0.01,
    magnitudes={
        "johnson_v": RemoteMagnitude(value=12.3, error=0.02, unit="mag"),
    },
    extra={"n_obs": 4},
    origin=RemoteOrigin(
        service="vizier",
        upstream_id="II/336/apass9",
        provider_version="APASS9 / DR9",
        server="vizier.cds.unistra.fr",
        queried_at=None,
        cache_status="disabled",
    ),
)
```

Object/name-resolution shape:

```python
RemoteObjectResult(
    service="simbad",
    input_name="M31",
    main_id="M  31",
    object_type="Galaxy",
    ra_deg=10.6847083,
    dec_deg=41.26875,
    coordinate_frame="ICRS",
    coordinate_epoch=None,
    origin=RemoteOrigin(...),
)
```

Notes:

- `separation_deg` is computed by Skycat after the remote service returns rows,
  using the same pure-Python spherical helper as tests use for local SQL
  distance checks. It is remote-query metadata, not a PostGIS value.
- Do not use local keys such as `release_id`, `release_name`, or local data-table
  columns unless their remote meaning is identical and documented.
- Include `origin` on every result. A persisted remote answer must show that it
  is not reproducible like a local imported release.

## 7. `CatalogRemoteReader` responsibilities

`CatalogRemoteReader` should own lifecycle and orchestration, mirroring
`CatalogReader` at the facade level while keeping semantics separate.

Responsibilities:

- Load `RemoteCatalogSettings` from constructor arguments or
  `SKYCAT_REMOTE_QUERY_*`.
- Hold the remote provider registry.
- Lazily construct backend clients. Constructing the reader must not perform a
  network call.
- Validate query inputs: RA, Dec, radius, limit, provider key, filters, order.
- Dispatch to the provider's backend.
- Normalize backend rows to typed remote result objects.
- Map known backend errors to the remote exception taxonomy.
- Expose `close()`, `__enter__`, and `__exit__` for any backend/session cleanup.

Initial API:

```python
reader = CatalogRemoteReader.from_env()

providers = reader.providers()
definition = reader.provider("apass")

rows = reader.cone(
    "apass",
    ra_deg=10.0,
    dec_deg=20.0,
    radius_arcmin=5.0,
    limit=100,
    order_by="johnson_v_mag",
    filters=[RemoteFilter("johnson_v_mag", "<=", 14.0)],
)

matches = reader.lookup("apass", native_id="123456", limit=10)

objects = reader.resolve_name("M31", service="simbad", limit=5)
```

Out of initial API:

- `crossmatch(...)`: raise `CatalogRemoteUnsupportedError` until TAP upload joins
  or another real batch primitive is designed.
- `release=...`: remote providers use service/upstream identity, not local
  release resolution.
- `fallback=...`: consumer policy, not provider behavior.

## 8. Backend responsibilities

All backends should implement a narrow protocol:

```python
class RemoteCatalogBackend(Protocol):
    service: str

    def cone(self, definition, request) -> RemoteTable:
        ...

    def lookup(self, definition, native_id, limit) -> RemoteTable:
        ...

    def close(self) -> None:
        ...
```

The backend returns a small adapter object (`RemoteTable`) rather than public
results. Normalization to `RemoteCatalogRow` stays in shared code so sentinel
handling, origin fields, and expression evaluation behave consistently.

### VizieR backend

Responsibilities:

- Use the declared `astroquery.vizier` dependency directly. Import/configure it
  in the backend path rather than at package import time to avoid import-time
  side effects, not to support missing installations.
- Build the exact requested column list from the provider definition, filters,
  and sort fields.
- Use instance-level `Vizier(...)` configuration. Do not mutate
  `Vizier.ROW_LIMIT`.
- Apply configured timeout, server/mirror, and cache behavior in one place.
- Translate `RemoteFilter` into VizieR filter strings only after field and
  operator validation.
- Treat malformed responses, missing required columns, and service failures as
  exceptions.
- Return empty result sets only when the service successfully returned no rows.

### SIMBAD backend

Responsibilities:

- Import and configure `astroquery.simbad.Simbad` lazily.
- Add required votable fields at first use, not at module import.
- Do not permanently disable SIMBAD after one transient initialization failure.
- Do not lowercase the caller's input name before sending it.
- Require astroquery behavior compatible with lowercase TAP columns
  (`main_id`, `ra`, `dec`, `otype`).
- Apply `limit` even if the upstream query method does not.
- Map object-type codes through a local table only as display metadata; preserve
  the raw code too.
- Surface coordinate epoch/proper-motion limitations explicitly in the result or
  result docs.

## 9. Data flow

Catalog cone:

```text
caller
  -> CatalogRemoteReader.cone()
  -> validate radius/coordinates/limit/provider
  -> load RemoteCatalogDef
  -> validate filters/order against provider definition
  -> derive required upstream columns
  -> backend.cone(definition, request)
  -> parse backend table
  -> normalize columns/expressions/sentinels
  -> compute separation_deg in Python
  -> attach RemoteOrigin
  -> return list[RemoteCatalogRow]
```

SIMBAD name resolution:

```text
caller
  -> CatalogRemoteReader.resolve_name()
  -> validate service/name/limit
  -> simbad backend query
  -> validate expected TAP column shape
  -> normalize coordinates and object type
  -> attach RemoteOrigin
  -> return list[RemoteObjectResult]
```

Unsupported operation:

```text
caller
  -> CatalogRemoteReader.crossmatch()
  -> provider capability check fails
  -> raise CatalogRemoteUnsupportedError
```

The important rule is that translation failures happen before the network call
and raise. They must not degrade into an over-broad query or an empty result.

## 10. Error handling

Add remote-specific exceptions in `skycat/remote/errors.py`.

Suggested taxonomy:

| Exception | Meaning | Exit-code mapping if a remote CLI is added |
|---|---|---|
| `CatalogRemoteError` | Base class for remote operational/query failures. Does not subclass `CatalogQueryError`. | 1 |
| `CatalogRemoteConfigError` | Invalid remote config, invalid dependency version, invalid custom provider definition. May subclass `CatalogConfigError` for code 2 mapping. | 2 |
| `CatalogRemoteProviderError` | Unknown provider, disabled provider, or provider cannot satisfy requested operation. | 1 |
| `CatalogRemoteUnsupportedError` | Capability intentionally not implemented, such as remote crossmatch. | 1 |
| `CatalogRemoteFilterError` | Unsafe, unknown, or untranslatable filter/order expression. | 1 |
| `CatalogRemoteServiceError` | Timeout, DNS, HTTP 5xx, rate limit, malformed response, missing required service columns. | 1 |

Handling rules:

- No `except Exception: pass`.
- No `print()` for service errors. Use exceptions and package loggers.
- Timeouts and retries are explicit settings. Retry only idempotent remote calls,
  with a small bounded count, and do not retry validation failures or clear 4xx
  caller errors.
- Rate limits should surface as rate-limit errors, not empty rows.
- Missing required columns are response/schema errors, not partial rows.
- Unknown filter fields/operators fail closed before the request is sent.
- Empty results are accepted only after a successful, fully translated request.

Existing code that catches `CatalogQueryError` should not accidentally catch a
CDS outage. Remote CLI commands can still map `CatalogRemoteError` to exit code
1 explicitly.

## 11. Configuration

Use a remote namespace that cannot be confused with the downstream routing
variables already seen in Skynet.

Recommended environment prefix:

- `SKYCAT_REMOTE_QUERY_TIMEOUT_S`
- `SKYCAT_REMOTE_QUERY_RETRIES`
- `SKYCAT_REMOTE_QUERY_USER_AGENT`
- `SKYCAT_REMOTE_QUERY_CACHE`
- `SKYCAT_REMOTE_QUERY_CACHE_DIR`
- `SKYCAT_REMOTE_QUERY_VIZIER_SERVER`
- `SKYCAT_REMOTE_QUERY_SIMBAD_SERVER`
- `SKYCAT_REMOTE_QUERY_PROVIDER_FILE`

Avoid `SKYCAT_REMOTE_FALLBACK`; it is already consumer routing policy and should
never be read by Skycat.

Defaults:

- Cache disabled in phase 1.
- No coordinate quantization, ever.
- Use explicit server/mirror only when configured; record the server actually
  used in `RemoteOrigin`.
- Remote provider file disabled until built-in definitions and validation tests
  exist.

## 12. Testing strategy

Default tests must be hermetic. Network tests should be separately marked and
skipped by default, with a `--require-remote` style escalation if added later.

Unit tests:

- Provider definition validation accepts shipped definitions and rejects broken
  shapes like string-valued `mags`.
- Expression mappings cover direct columns, arithmetic, sexagesimal parsing,
  sentinel-to-`None`, and identifier templates.
- Rows with no magnitudes are retained.
- `RemoteFilter` validates column/operator/value and translates numeric filters
  into explicit VizieR filter syntax.
- Unknown filters and unsupported order fields raise before backend calls.
- `CatalogRemoteReader` construction does not touch the network or perform
  service validation.
- Dependency version guards produce `CatalogRemoteConfigError` before a backend
  sends a request.

Backend tests with fixtures:

- Use recorded VOTable/CSV/Astropy-table fixtures for APASS and one small
  coordinate-sensitive catalog such as Landolt or Stetson.
- Assert non-zero expected result counts for known fixture queries.
- Assert magnitude filters reduce or preserve result counts in the intended
  direction; this catches equality-filter mistakes.
- Assert required origin fields are present.
- Assert missing required upstream columns raise `CatalogRemoteServiceError`.
- Assert timeout/rate-limit/malformed-response adapters raise typed errors.

SIMBAD tests:

- Fixture lowercase TAP columns and reject the old uppercase/sexagesimal shape
  unless explicitly supported by a compatibility branch.
- Verify `limit` is applied.
- Verify transient initialization failure does not permanently disable future
  calls.
- Verify input name case is preserved.

Local/remote parity tests:

- For local catalogs that intentionally mirror a VizieR table, compare a small
  fixture of local parser output against recorded VizieR rows for coordinate
  conversion and native-id normalization.
- Landolt and Stetson deserve first coverage because their parser docstrings
  claim remote-coordinate parity.

Live tests:

- Mark as `remote_live`.
- Run only on demand.
- Keep them small: one SIMBAD name, one VizieR APASS cone, one expected filter.
- Assert non-zero rows where appropriate.

Quality gates:

- `uv run ruff check skycat tests`
- `uv run pyright skycat`
- `uv run pytest tests -q -m "not postgis and not remote_live"`

## 13. Migration and compatibility steps

No database migration is needed for the remote reader. Remote query code does
not write catalog tables or registry rows.

Compatibility plan:

1. Add `skycat/remote/` without changing `CatalogReader`, `skycat.query.__all__`,
   or existing CLI commands.
2. Add the required remote dependencies to the main project dependency set with
   explicit version bounds, then update and review the lockfile.
3. Confirm package import and existing local CLI commands still have no network
   side effects.
4. Keep remote exports under `skycat.remote` while the surface is experimental.
5. Add stable top-level export only after result shape, exception taxonomy, and
   CLI/docs are settled.
6. If `radius_to_deg` or other pure helpers move out of `skycat/query/cone.py`,
   keep the old import path as a re-export to preserve the documented local API.
7. Add remote configuration documentation with a separate table that explicitly
   distinguishes Skycat-read variables from downstream consumer variables.
8. Add changelog and API-stability notes before declaring the remote reader
   stable.

## 14. Phased implementation tasks

### Phase 0: decisions before code

- Confirm public class name: `CatalogRemoteReader`.
- Decide the `astroquery` version floor/ceiling and transitive dependency impact.
- Decide first supported provider set. Recommended:
  - SIMBAD name resolution first.
  - VizieR APASS DR9 as the first catalog-row provider.
  - Landolt/Stetson next for local/remote parity tests.
- Decide provider-key policy for ambiguous names such as APASS.
- Decide whether custom provider definitions are phase 1 or later. Recommended:
  later, after built-in schema validation is proven.

### Phase 1: remote core with no network

- Create `skycat/remote/` modules for definitions, results, filters, errors, and
  registry.
- Add validation for provider definitions.
- Add expression/mapping primitives.
- Add fake backend tests for reader dispatch and normalization.
- Add built-in APASS definition as data, but do not call VizieR yet.

### Phase 2: SIMBAD resolver

- Add dependency version checks for the supported `astroquery` behavior.
- Implement lazy SIMBAD backend initialization.
- Add `CatalogRemoteReader.resolve_name()`.
- Add fixture tests for column shape, limit handling, error mapping, and no
  permanent disable after transient initialization failure.
- Add one manual live test marker.

### Phase 3: VizieR cone and lookup

- Implement VizieR backend with instance-level `Vizier` configuration.
- Implement APASS DR9 remote provider.
- Normalize rows to `RemoteCatalogRow`.
- Add fixture tests for APASS row count, magnitude filtering, sentinel handling,
  origin metadata, and no row drops for missing magnitudes.
- Add one manual live test marker.

### Phase 4: provider expansion and parity

- Add Landolt and Stetson remote providers.
- Add recorded fixture comparisons against local parser output for coordinate
  and identifier parity.
- Add Pan-STARRS, SkyMapper, 2MASS, Tycho-2, UCAC5, USNO-B1.0, and VSX only
  after each definition has validation fixtures and role/capability labels.
- Mark VSX as metadata/exclusion, not calibration.

### Phase 5: operator and CLI surface

- Add `skycat remote providers`.
- Add `skycat remote cone`.
- Add `skycat remote lookup` only for providers whose native-id lookup is
  defined and tested.
- Add `skycat remote resolve-name`.
- Map remote exceptions to the established exit-code taxonomy.
- Update stable docs only when the CLI and Python surface are ready to be
  treated as current behavior.

### Phase 6: batch/TAP investigation

- Answer whether VizieR TAP supports upload joins in a way that can satisfy
  Skycat crossmatch semantics.
- If yes, design a separate TAP batch path with fixtures and limits.
- If no, document remote crossmatch as unsupported and leave batching to local
  mirrored catalogs or consumer-specific workflows.

## 15. Acceptance criteria

The first remote release should not be considered complete until these are true:

- Required remote dependencies are declared in the main project dependency set
  with reviewed version bounds.
- Constructing `CatalogRemoteReader` performs no network I/O.
- An invalid provider definition fails at load time.
- An invalid filter fails before a network call.
- A network timeout raises a typed remote service error.
- SIMBAD failures do not permanently disable the resolver for the process.
- Remote APASS results identify VizieR APASS9/DR9 explicitly.
- Remote rows with coordinates but no magnitudes are returned, not dropped.
- Every result includes `RemoteOrigin`.
- Default CI remains network-free.
- No implementation subclasses `CatalogQueryError` for third-party service
  outages.
