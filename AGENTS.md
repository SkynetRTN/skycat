# Repository Guidelines

## Project Structure & Module Organization

Skycat is a Python package and CLI for local PostgreSQL/PostGIS-backed astronomical catalogs. Source code lives in `skycat/`: `cli/` contains Click commands, `database/` and `migrations/` manage schemas and Alembic revisions, `ingestion/` handles discovery, parsing, and COPY loading, and `query/`, `registry/`, `models/`, `spatial/`, and `validation/` hold the domain logic. Tests are under `tests/`, with small fixtures in `tests/data/`. Stable documentation is in `docs/`; `docs/working/` is for planning notes. Deployment and local database assets live in `infra/docker/` and `infra/kubernetes/`; branding assets are in `brand/`.

## Build, Test, and Development Commands

- `uv sync --extra dev`: install the project and dev tools.
- `uv run skycat --help`: inspect the CLI entry point.
- `uv run ruff check skycat tests`: run the configured lint gate.
- `uv run pyright skycat`: run the configured type check.
- `uv run pytest tests -q -m "not postgis"`: run fast tests without a database.
- `uv run pytest tests -q --require-postgis`: run the full suite and fail if PostGIS is unavailable.
- `uv build`: build the wheel and sdist.

Use a disposable PostGIS database for integration tests; fixtures may replace imported catalog data.

## Coding Style & Naming Conventions

Target Python 3.11+ and follow the Ruff settings in `pyproject.toml` (`line-length = 110`). Use four-space indentation, `snake_case` for functions and modules, `PascalCase` for classes, and descriptive constants. Preserve domain naming conventions such as units in column names (`ra_err_arcsec`, `johnson_v_mag`). Parser code should stream rows instead of materializing catalog files. Broad exception handling is intentionally restricted; only add per-file ignores with a clear reason.

## Testing Guidelines

Pytest discovers `tests/test_*.py`. Mark database-dependent tests with `postgis`; local green runs without `--require-postgis` may skip integration coverage. Keep `tests/test_migration_graph.py` passing for any Alembic change, and update docs with CLI or API changes because docs are tested.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects, sometimes scoped (`docs: ...`). Keep commits focused and open PRs against `dev` unless told otherwise. PRs should summarize behavior changes, list checks run, and link relevant issues. Schema changes need Alembic migrations with one head. Do not commit catalog source datasets, database dumps, credentials, staging output, or generated import artifacts.

## Security & Configuration Tips

Use `SKYCAT_DB_*` for database settings and `SKYCAT_DATA_ROOT` for source catalogs. Keep real secrets out of `.env`, docs, and examples; use the templates under `infra/docker/` for local setup.
