"""Architectural isolation from skynet-db and the primary `sky` database."""

from __future__ import annotations

import sys


def test_importing_catalogs_does_not_import_skynet_db():
    # Importing the whole package (incl. models) must not pull in skynet_db.
    import skynet_catalogs  # noqa: F401
    import skynet_catalogs.models  # noqa: F401

    assert "skynet_db" not in sys.modules


def test_separate_metadata_and_base():
    from skynet_catalogs.database.base import CatalogBase, catalog_metadata

    assert CatalogBase.metadata is catalog_metadata
    # No skynet_db Base in the MRO / module graph.
    assert "skynet_db" not in type(CatalogBase).__module__


def test_all_tables_live_in_catalog_schemas():
    from skynet_catalogs.database.base import CatalogBase

    schemas = {t.schema for t in CatalogBase.metadata.tables.values()}
    assert schemas == {"catalog_registry", "catalog_data"}
    # data tables are partitioned parents in catalog_data
    names = set(CatalogBase.metadata.tables)
    assert "catalog_data.apass_source" in names
    assert "catalog_data.vsx_source" in names
    assert "catalog_registry.catalog_family" in names


def test_no_catalog_object_model_reused():
    # We define our own ApassSource/VsxSource — not skynet_db's CatalogObject.
    from skynet_catalogs import models

    assert hasattr(models, "ApassSource")
    assert hasattr(models, "VsxSource")
    assert not hasattr(models, "CatalogObject")
