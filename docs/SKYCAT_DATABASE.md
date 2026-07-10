# Skycat database

A **standalone** PostgreSQL/PostGIS store for large local astronomical reference
catalogs (APASS DR6/DR10, VSX, and future families). It is architecturally
separate from `skynet-db` and the primary `sky` database — its own database
(`catalogs`), Compose service (`skycat-postgres`, host port **5433**), volume
(`catalog_postgres_data`), SQLAlchemy metadata, Alembic environment, roles, and
configuration namespace (`SKYCAT_DB_*`).

The package and its full documentation live at
[`packages/py/skycat/`](https://github.com/skynetrtn/skynet/tree/main/packages/py/skycat)
(see its `README.md`). This page is the operator quick-reference.

> **Bringing the local catalog online for the optical pipeline?** Follow the
> step-by-step Phase-1 rollout runbook in
> [`SKYCAT_LOCAL_ROLLOUT.md`](SKYCAT_LOCAL_ROLLOUT.md): install the worker
> dependency, provision + initialise the DB, import/validate/activate APASS
> DR6+DR10 and VSX, run the worker in strict local-only mode, and prove no
> remote catalog access occurs.

## Architecture

```
postgres        db: sky        127.0.0.1:5432  vol postgres_data         (operational)
skycat-postgres db: catalogs  127.0.0.1:5433  vol catalog_postgres_data (reference catalogs)
```

* Schemas: `catalog_registry` (families/releases/runs/validation/manifests),
  `catalog_data` (release-partitioned typed tables), `catalog_staging` (bulk load).
* Roles: `catalog_owner` (migrator), `catalog_ingest` (loader), `catalog_reader`
  (read-only), plus a bootstrap DBA for `init`.
* Spatial: `geography(Point,4326)` GENERATED column + GiST; spherical cone search
  (correct across RA 0/360 and the poles).
* Each catalog family is one `LIST (release_id)` partitioned table; activation
  flips a registry flag (atomic) and never rewrites rows.

## Local Docker Compose

```bash
cp infra/docker/.env.example         infra/docker/.env
cp infra/docker/.env.secrets.example infra/docker/.env.secrets    # fill secrets

docker compose -f infra/docker/compose.yaml build skycat-postgres
docker compose -f infra/docker/compose.yaml up -d skycat-postgres

# init (PostGIS + roles + grants + migrate), then import + query
docker compose -f infra/docker/compose.yaml --profile skycat-tools run --rm skycat-init
docker compose -f infra/docker/compose.yaml --profile skycat-tools run --rm skycat-import apass dr6 --activate
docker compose -f infra/docker/compose.yaml --profile skycat-tools run --rm \
  skycat cone apass --ra 10 --dec 1 --radius-arcmin 5

# stop without deleting data
docker compose -f infra/docker/compose.yaml stop skycat-postgres
# deliberately delete the volume (explicit; `down` never removes it)
docker volume rm skynet_catalog_postgres_data
```

Source datasets are mounted **read-only** at `/catalog-data`
(`SKYCAT_DATA_ROOT`); the host default is `/srv/agents/catalogs`.

## Kubernetes

Connection settings come from `skynet-shared-config` (non-secret
`SKYCAT_DB_*`) and `skynet-runtime-secrets` (passwords). On-demand jobs
(not part of the base kustomization, never run on app startup):

```bash
kubectl apply -f infra/kubernetes/deploy/base/jobs/skycat-init.yaml     # DBA: postgis+roles+grants+migrate
kubectl apply -f infra/kubernetes/deploy/base/jobs/skycat-migrate.yaml  # owner: alembic upgrade
kubectl apply -f infra/kubernetes/deploy/base/jobs/skycat-ingest.yaml   # ingest: import one family/release
```

## Production provisioning

The catalog database is externally provisioned (like `sky`). The Ansible
`postgres_server` role provisions the `catalogs` database, the bootstrap DBA
role, and PostGIS when `pg_catalog_enabled: true` (group_vars). The package
`init`/`migrate` jobs then create the operational roles and apply migrations.

## Safety

`docker compose down` never deletes the catalog volume; active releases can't be
dropped without `--force`; the package refuses to migrate/ingest against `sky`;
production-like targets get extra guards; source files are read-only and never
modified or deleted.
