FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS deps

ENV UV_PROJECT_ENVIRONMENT=/opt/venvs/skycat
ENV UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN mkdir -p skycat \
    && touch skycat/__init__.py \
    && uv sync --frozen --no-dev --no-install-project

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime

LABEL org.opencontainers.image.source="https://github.com/SkynetRTN/skycat"

ENV UV_PROJECT_ENVIRONMENT=/opt/venvs/skycat
ENV PATH="/opt/venvs/skycat/bin:${PATH}"
ENV UV_LINK_MODE=copy
WORKDIR /app

RUN useradd --create-home --uid 10001 skycat

COPY --from=deps /opt/venvs/skycat /opt/venvs/skycat
COPY . .
RUN uv sync --frozen --no-dev

USER skycat

# `skycat <subcommand>` — e.g. init / migrate / import / health.
ENTRYPOINT ["skycat"]
CMD ["health"]
