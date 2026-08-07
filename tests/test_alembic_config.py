"""The Alembic bridge must survive a password a generator would emit. No database.

`CatalogDatabaseConfig.url()` percent-encodes the password, and Alembic's
`Config.set_main_option` writes through `configparser`, whose `BasicInterpolation`
rejects any `%` that is not `%%` or `%(name)s`. Every escape `quote()` produces is
exactly that, so `@`, `!`, `#`, `:`, `$`, `&`, `+`, a space, or `%` in the admin
password used to kill `skycat init` (at the migrate step), `skycat migrate`,
`skycat migrate-status`, the `skycat-migrate` Kubernetes Job, and the
`migrations_current` health check — with a `ValueError` from configparser, several
frames from anything an operator could act on.

Nothing in CI saw it: every credential in the workflows and fixtures is
`[A-Za-z0-9_]`, which is precisely why it survived.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url

from skycat.config import CatalogDatabaseConfig
from skycat.database.migrate import make_alembic_config, script_heads

#: One password carrying every character class `quote()` escapes. `/` is left
#: out on purpose: `quote()` treats it as safe, so it is a separate (pre-existing)
#: URL-construction question and not what this module is about.
NASTY = "p@ss w!rd#1:2$3&4+5%6"


def _config() -> CatalogDatabaseConfig:
    return CatalogDatabaseConfig(
        host="127.0.0.1", port=5999, name="catalogs",
        user="catalog_owner", password=NASTY,
    )


def test_building_the_config_does_not_raise():
    make_alembic_config(_config())


def test_the_url_alembic_reads_back_is_the_url_we_gave_it():
    """Escaping for configparser must not leak into what Alembic dials."""
    cfg = _config()
    url = make_alembic_config(cfg).get_main_option("sqlalchemy.url")
    assert url == cfg.url()
    assert make_url(url).password == NASTY


def test_script_heads_works_with_a_generated_password():
    """`migrate-status`'s database-free half — it builds the same config."""
    assert script_heads(_config())
