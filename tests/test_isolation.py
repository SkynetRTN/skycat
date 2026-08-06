"""Architectural isolation for the standalone catalog schema."""

from __future__ import annotations


def test_importing_catalogs_keeps_catalog_metadata_local():
    # Importing the whole package (incl. models) should register tables only on
    # Skycat's own metadata.
    import skycat  # noqa: F401
    import skycat.models  # noqa: F401

    from skycat.database.base import CatalogBase, catalog_metadata

    assert CatalogBase.metadata is catalog_metadata


def test_separate_metadata_and_base():
    from skycat.database.base import CatalogBase, catalog_metadata

    assert CatalogBase.metadata is catalog_metadata
    assert CatalogBase.__module__ == "skycat.database.base"


def test_all_tables_live_in_catalog_schemas():
    from skycat.database.base import CatalogBase

    schemas = {t.schema for t in CatalogBase.metadata.tables.values()}
    assert schemas == {"catalog_registry", "catalog_data"}
    # data tables are partitioned parents in catalog_data
    names = set(CatalogBase.metadata.tables)
    assert "catalog_data.apass_source" in names
    assert "catalog_data.vsx_source" in names
    assert "catalog_registry.catalog_family" in names


def test_catalog_family_models_are_explicit():
    # Each supported family has its own typed model instead of reusing a generic
    # catalog-object table.
    from skycat import models

    assert hasattr(models, "ApassSource")
    assert hasattr(models, "VsxSource")
    assert not hasattr(models, "CatalogObject")
