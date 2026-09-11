# syntax=docker/dockerfile:1

# ---- builder -------------------------------------------------------------
# Dependencies are installed into a virtualenv that the runtime stage copies,
# so build tooling never reaches the final image.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencies first, in their own layer: this is only invalidated when the
# lockfile changes, so source edits do not trigger a full reinstall.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# The embedding model's weights, fetched here so a worker never downloads them at
# startup. This layer depends only on the lockfile, so editing source never re-fetches
# the 63 MB. The name repeats EMBEDDING_MODEL in app/services/embeddings.py: the worker
# image runs offline, so a mismatch fails at startup instead of downloading.
RUN .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/models')"

COPY README.md alembic.ini ./
COPY app ./app
COPY migrations ./migrations
RUN uv sync --frozen --no-dev

# ---- runtime -------------------------------------------------------------
# Everything the api and worker images share: the same interpreter, the same
# virtualenv, the same non-root user. Only the entrypoint differs between them.
FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/app /app/app

USER app

# ---- worker --------------------------------------------------------------
# No migrations and no port: the worker consumes a schema the api image manages,
# and serves no traffic. It does carry the embedding weights, and runs with the
# Hugging Face Hub offline so a missing model fails at startup rather than downloading.
FROM runtime AS worker

COPY --from=builder --chown=app:app /app/models /app/models
ENV EMBEDDING_CACHE_DIR=/app/models \
    HF_HUB_OFFLINE=1

CMD ["python", "-m", "app.worker"]

# ---- api -----------------------------------------------------------------
# Last on purpose, so a `docker build` with no --target still produces the api
# image rather than silently switching to the worker.
FROM runtime AS api

# Migrations ship in the image so a container can migrate its own database.
COPY --from=builder --chown=app:app /app/migrations /app/migrations
COPY --from=builder --chown=app:app /app/alembic.ini /app/alembic.ini

EXPOSE 8000

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
