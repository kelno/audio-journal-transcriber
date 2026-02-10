# Inspired from https://depot.dev/docs/container-builds/optimal-dockerfiles/python-uv-dockerfile

# ===========================
# Stage 1 : Builder
# ===========================
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ARG VERSION=dev
LABEL version="$VERSION"

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app

COPY uv.lock pyproject.toml ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable


# ===========================
# Stage 2 : Runtime
# ===========================
FROM python:3.12-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH"

# Install ffmpeg and any minimal dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /bin/bash --home-dir /app appuser

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

USER appuser

# Entrypoint: run the transcriber module
ENTRYPOINT ["python", "-m", "uv", "run", "transcriber"]
CMD []
