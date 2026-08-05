# Skycat catalog database image: PostgreSQL 16 + PostGIS 3.5.
#
# This is the *local-development* implementation of the catalog database
# (Compose service `skycat-postgres`, host port 5433). Production / staging
# use an externally-provisioned PostgreSQL/PostGIS service exposing the same
# connection + role contract (see README.md).
#
# Based on `imresamu/postgis` — the multi-arch (amd64 + arm64) continuation of
# the official `postgis/postgis` images, recommended by the PostGIS project for
# ARM. It builds/runs on amd64 CI/prod *and* arm64 (Apple Silicon) dev machines,
# unlike the amd64-only `postgis/postgis` tags. PostgreSQL 16 matches the local
# PostgreSQL major version used for local development and CI.
FROM imresamu/postgis:16-3.5
LABEL org.opencontainers.image.source="https://github.com/SkynetRTN/skycat"

# Dev-only bootstrap role + database, mirroring the `postgres` (sky) image's
# pattern of baking well-known *development* credentials. Override every value
# in Compose / production; never put real credentials here. The catalog store
# uses a SEPARATE database (`catalogs`) and a SEPARATE volume from `sky`.
ENV POSTGRES_USER=catalog_admin
ENV POSTGRES_PASSWORD=catalog
ENV POSTGRES_DB=catalogs

# Enable PostGIS on first-boot init (idempotent; `skycat init` also
# ensures it). Runs as the bootstrap superuser in the `catalogs` database.
COPY scripts/skycat-postgis-init.sql /docker-entrypoint-initdb.d/10-postgis.sql

EXPOSE 5432
