# syntax=docker/dockerfile:1.6
FROM python:3.11-slim

WORKDIR /app

# Install git (needed for gitpython). Use BuildKit cache mount for apt
# so package lists/debs persist across builds — a clean rebuild that used
# to take ~140s now takes a few seconds.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps with a persistent pip cache. As long as requirements.txt
# is unchanged, the layer is cached and pip is skipped entirely. On a cache
# miss (changed requirements), wheels are pulled from the local cache instead
# of PyPI — turning a 19-min install into ~1 min.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY . .

# Create data directories
RUN mkdir -p /app/chroma_db /app/repo_cache /app/data

EXPOSE 8000

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
