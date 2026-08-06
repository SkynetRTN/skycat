"""Configuration parsing, URL construction, role resolution, and guards."""

from __future__ import annotations

import pytest

from skycat.config import (
    CatalogConfigError,
    CatalogDatabaseConfig,
    CatalogRole,
    CatalogSettings,
)


def test_default_host_is_host_oriented():
    cfg = CatalogDatabaseConfig()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 5433
    assert cfg.name == "catalogs"


def test_url_construction_and_redaction():
    cfg = CatalogDatabaseConfig(
        backend="postgresql+psycopg",
        host="skycat-postgres",
        port=5432,
        name="catalogs",
        user="catalog_reader",
        password="p@ss/w:rd",
    )
    url = cfg.url()
    assert url.startswith("postgresql+psycopg://catalog_reader:")
    assert "@skycat-postgres:5432/catalogs" in url
    # special chars are percent-encoded
    assert "p%40ss" in url
    assert "***" in cfg.safe_url()
    assert "p@ss" not in cfg.safe_url()


def test_sslmode_appended():
    cfg = CatalogDatabaseConfig(sslmode="require")
    assert "sslmode=require" in cfg.url()


def test_docker_internal_vs_host_configs():
    internal = CatalogDatabaseConfig(host="skycat-postgres", port=5432)
    host = CatalogDatabaseConfig(host="127.0.0.1", port=5433)
    assert ":5432/catalogs" in internal.url()
    assert ":5433/catalogs" in host.url()


def test_refuses_reserved_database():
    with pytest.raises(CatalogConfigError):
        CatalogDatabaseConfig(name="postgres").assert_not_reserved_database()
    # case-insensitive
    with pytest.raises(CatalogConfigError):
        CatalogDatabaseConfig(name="TEMPLATE1").assert_not_reserved_database()
    # catalogs is fine
    CatalogDatabaseConfig(name="catalogs").assert_not_reserved_database()


def test_production_like_detection():
    assert CatalogDatabaseConfig(host="catalog-prod01.example.edu").looks_production()
    assert CatalogDatabaseConfig(name="catalogs_staging").looks_production()
    assert not CatalogDatabaseConfig(
        host="127.0.0.1", name="catalogs"
    ).looks_production()


def test_role_resolution_from_env(monkeypatch):
    monkeypatch.setenv("SKYCAT_DB_HOST", "skycat-postgres")
    monkeypatch.setenv("SKYCAT_DB_PORT", "5432")
    monkeypatch.setenv("SKYCAT_DB_USER", "catalog_reader")
    monkeypatch.setenv("SKYCAT_DB_PASSWORD", "rpw")
    monkeypatch.setenv("SKYCAT_DB_ADMIN_USER", "catalog_owner")
    monkeypatch.setenv("SKYCAT_DB_ADMIN_PASSWORD", "opw")
    monkeypatch.setenv("SKYCAT_DB_INGEST_USER", "catalog_ingest")
    monkeypatch.setenv("SKYCAT_DB_INGEST_PASSWORD", "ipw")
    s = CatalogSettings.from_env()
    assert s.config_for(CatalogRole.READER).user == "catalog_reader"
    assert s.config_for(CatalogRole.ADMIN).user == "catalog_owner"
    assert s.config_for(CatalogRole.ADMIN).password == "opw"
    assert s.config_for(CatalogRole.INGEST).user == "catalog_ingest"
    # default identity == reader credentials
    assert s.config_for(CatalogRole.DEFAULT).user == "catalog_reader"


def test_role_falls_back_to_default_when_unset(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("SKYCAT_DB_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SKYCAT_DB_USER", "only_user")
    monkeypatch.setenv("SKYCAT_DB_PASSWORD", "only_pw")
    s = CatalogSettings.from_env()
    # No ADMIN creds -> falls back to the default identity.
    assert s.config_for(CatalogRole.ADMIN).user == "only_user"
