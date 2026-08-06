# Contributing

Skycat is a public repository with a closed maintenance model. Anyone may open a
pull request, but maintainers decide what enters `dev` and `main`.

## Pull requests

- Open pull requests against `dev` unless a maintainer asks otherwise.
- Keep changes focused; separate docs, packaging, migrations, and behavior
  changes when practical.
- Do not commit catalog source datasets, database dumps, credentials, staging
  output, or generated import artifacts.
- For schema changes, include Alembic migrations and keep the migration graph to
  one head.
- For package/release changes, make sure installed-wheel behavior is covered.

## Local checks

Run the smallest relevant checks before opening a PR:

```bash
uv run ruff check skycat tests
uv run pyright skycat
uv run pytest tests -q -m "not postgis"
uv run pytest tests/test_migration_graph.py -q
uv build
```

The full PostGIS suite needs a disposable PostgreSQL/PostGIS database. Do not
point integration tests at a catalog database that contains data you care about.

## Code ownership

`.github/CODEOWNERS` marks workflow definitions, migrations, deployment assets,
packaging metadata, and the dependency lockfile as maintainer-owned paths.
Branch protection should require Code Owner review for those paths.
