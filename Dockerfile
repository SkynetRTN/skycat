ARG PYTHON_BASE_IMAGE=skynet-python-base:latest

# Image for the catalog CLI — used by the Kubernetes catalog jobs
# (init / migrate / ingest) and as a generic catalog ops container. Built FROM
# the shared python-base. skycat has NO dependency on skynet-db/skylib;
# it carries only its own dependency closure (sqlalchemy, geoalchemy2, psycopg,
# alembic, click).
FROM ${PYTHON_BASE_IMAGE} AS deps
ENV UV_PROJECT_ENVIRONMENT=/opt/venvs/skycat
WORKDIR /app/packages/py/skycat

COPY packages/py/skycat/pyproject.toml packages/py/skycat/uv.lock ./
# A minimal package tree is enough for `uv sync` to resolve + install deps.
COPY packages/py/skycat/README.md ./
RUN mkdir -p skycat && touch skycat/__init__.py \
    && uv sync --frozen --no-install-project

FROM ${PYTHON_BASE_IMAGE} AS runtime
LABEL org.opencontainers.image.source="https://github.com/skynetrtn/skynet"
ENV UV_PROJECT_ENVIRONMENT=/opt/venvs/skycat
WORKDIR /app/packages/py/skycat

COPY --from=deps --chown=skynet:skynet /opt/venvs/skycat /opt/venvs/skycat
COPY --chown=skynet:skynet packages/py/skycat /app/packages/py/skycat

USER skynet
RUN uv sync --frozen

# `skycat <subcommand>` — e.g. init / migrate / import / health.
ENTRYPOINT ["uv", "run", "--project", "/app/packages/py/skycat", "skycat"]
CMD ["health"]
