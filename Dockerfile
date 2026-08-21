# Inspired from https://depot.dev/docs/container-builds/optimal-dockerfiles/python-uv-dockerfile

# ===========================
# Stage 1 : Builder
# ===========================
# https://docs.astral.sh/uv/guides/integration/docker/#available-images
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS builder

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
FROM python:3.14-slim-trixie

ENV PATH="/app/.venv/bin:$PATH"
# Containers accept connections on every interface by default. docker run -e
# can override this image setting without changing the packaged application default.
ENV TRANSCRIBER_HTTP__HOST="0.0.0.0"

# Install ffmpeg and any minimal dependencies. (Improve me: It has a lot of unecessary stuff for our purpose and bloats the image size)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        && rm -rf /var/lib/apt/lists/* \
    && ffmpeg -version

WORKDIR /app
RUN mkdir -p /app \
    && chmod 755 /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
# (OpenShift) Change permissions to group 0 (root) and allow users in the root group to access files in /app
RUN chgrp -R 0 /app && chmod -R g=u /app

# Document the FastAPI port used by the default continuous mode.
EXPOSE 8765/tcp

# Continuous watching and local HTTP serving are the transcriber's default mode.
ENTRYPOINT ["transcriber"]
CMD []
