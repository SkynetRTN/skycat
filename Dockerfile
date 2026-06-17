ARG PYTHON_BASE_IMAGE=skynet-python-base:latest

# Image for the catalog CLI — used by the Kubernetes catalog jobs
# (init / migrate / ingest) and as a generic catalog ops container. Built FROM
# the shared python-base. skynet-catalogs has NO dependency on skynet-db/skylib;
# it carries only its own dependency closure (sqlalchemy, geoalchemy2, psycopg,
# alembic, click).
FROM ${PYTHON_BASE_IMAGE} AS deps
ENV UV_PROJECT_ENVIRONMENT=/opt/venvs/skynet-catalogs
WORKDIR /app/packages/py/skynet-catalogs

COPY packages/py/skynet-catalogs/pyproject.toml ./
# A minimal package tree is enough for `uv sync` to resolve + install deps.
COPY packages/py/skynet-catalogs/README.md ./
RUN mkdir -p skynet_catalogs && touch skynet_catalogs/__init__.py \
    && uv sync --no-install-project

FROM ${PYTHON_BASE_IMAGE} AS runtime
LABEL org.opencontainers.image.source="https://github.com/skynetrtn/skynet"
ENV UV_PROJECT_ENVIRONMENT=/opt/venvs/skynet-catalogs
WORKDIR /app/packages/py/skynet-catalogs

COPY --from=deps --chown=skynet:skynet /opt/venvs/skynet-catalogs /opt/venvs/skynet-catalogs
COPY --chown=skynet:skynet packages/py/skynet-catalogs /app/packages/py/skynet-catalogs

USER skynet
RUN uv sync --frozen 2>/dev/null || uv sync

# `skynet-catalogs <subcommand>` — e.g. init / migrate / import / health.
ENTRYPOINT ["uv", "run", "--project", "/app/packages/py/skynet-catalogs", "skynet-catalogs"]
CMD ["health"]
