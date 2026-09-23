# The pinned runtime image: the gateway and the worker run from it (I2B.a).
#
#   docker build --provenance=false -f deploy/Dockerfile -t infrx-runtime:<git-sha> apps/infrx-api
#
# Every input is pinned: both base images by registry digest (the tag beside each is a
# comment - the tag -> digest resolution is recorded in the I2B evidence), the Python
# dependencies by `uv.lock` through `--frozen`, and the application by the git tree the
# context is built from. The installer records the resulting image id in the env file
# (`INFRX_IMAGE=sha256:…`), so a unit never runs "whatever is tagged latest".
#
# Python 3.12.14 >= the 3.12.4 pin of `preflight.REQUIRED_PYTHON` (M1: older 3.12 builds
# answer `is_private` from stale special-purpose tables); the probe re-checks it inside
# this image at every install.
FROM ghcr.io/astral-sh/uv:0.11.8@sha256:3b7b60a81d3c57ef471703e5c83fd4aaa33abcd403596fb22ab07db85ae91347 AS uv

FROM python:3.12.14-slim-trixie@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

# Least privilege: one uid per process role, one shared group for the media root the
# gateway writes and the worker reads. No process in this image runs as root.
RUN groupadd --system --gid 10000 infrx \
 && useradd --system --uid 10001 --gid infrx --no-create-home --shell /usr/sbin/nologin infrx-gateway \
 && useradd --system --uid 10002 --gid infrx --no-create-home --shell /usr/sbin/nologin infrx-worker

ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
# `state` (psycopg) and `scheduling` (valkey) are what the pilot processes import; traces
# and judge stay out until their services are deployed. uv itself does not ship.
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --frozen --no-dev --extra state --extra scheduling
COPY openrouter ./openrouter
COPY infrx ./infrx
COPY deploy/preflight.py deploy/migrate.py ./deploy/
RUN python -m compileall -q infrx deploy

USER infrx-gateway:infrx
