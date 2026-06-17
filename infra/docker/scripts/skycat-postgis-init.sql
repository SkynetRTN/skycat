-- First-boot initialization for the catalog-postgres dev image.
-- Runs (by the PostgreSQL entrypoint) as the bootstrap superuser in the
-- `catalogs` database. Enabling PostGIS here makes the container spatial-ready
-- immediately; `skynet-catalogs init` repeats it idempotently and also creates
-- the operational roles, grants, schemas, and applies migrations.
CREATE EXTENSION IF NOT EXISTS postgis;
