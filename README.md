# Groundwork

A production-style RAG knowledge service. Users upload documents, the system indexes
them asynchronously, and questions are answered from retrieved source material with
citations back to the originating document and page.

**Status:** in progress — see [docs/success-criteria.md](docs/success-criteria.md) for the
targets this project is being measured against.

## Stack

| Layer | Choice |
| --- | --- |
| API | FastAPI |
| Database | PostgreSQL 16 + pgvector |
| Queue | PostgreSQL (`FOR UPDATE SKIP LOCKED`) |
| Object storage | S3 (MinIO locally) |
| Embeddings | fastembed / bge-small-en-v1.5 |
| Generation | Claude |
| Orchestration | Docker Compose, Kubernetes |
| Cloud | EKS, ECR, RDS, S3 |

## Development

Requires [uv](https://docs.astral.sh/uv/), Python 3.12, and Docker.

```bash
cp .env.example .env        # placeholder secrets work locally; change them for anything shared
uv sync                     # install dependencies
```

### Checks

```bash
uv run ruff check .         # lint
uv run ruff format --check .
uv run mypy .               # type check
uv run pytest               # tests
```

Unit tests always run. Integration tests need PostgreSQL, MinIO, and the embedding
model, and skip with a message when any of them is unavailable. To run them:

```bash
docker compose up -d postgres minio createbucket   # data services and the bucket
uv run alembic upgrade head                        # schema
uv run pytest
```

The first run downloads the embedding model (about 63 MB). Set `EMBEDDING_CACHE_DIR` to
keep it somewhere that survives restarts.

Start only these services for tests. A running `worker` container claims the jobs the
integration tests create, and they fail unpredictably.

Tests that call the real Claude API are excluded from every run unless selected, since
each run costs money. CI runs them on pushes to `main` and on demand; to run them
locally, export a key first:

```bash
ANTHROPIC_API_KEY=... uv run pytest -m live tests/live
```

### Running the service

The API answers with Claude by default, and refuses to start without `ANTHROPIC_API_KEY`
in `.env`. Set `GENERATOR=stub` there to run without a key; answers are then the opening
words of the top retrieved passage.

```bash
docker compose up -d --build
uv run alembic upgrade head
curl -s -X POST localhost:8000/collections -H 'content-type: application/json' -d '{"name": "demo"}'
curl -s -X POST localhost:8000/documents -F collection_id=<collection id> -F file=@report.pdf
curl -s localhost:8000/jobs/<job id>        # queued, running, then completed or failed
curl -s -X POST localhost:8000/questions -H 'content-type: application/json' \
  -d '{"collection_id": "<collection id>", "question": "What does the report conclude?"}'
```

Stop the `api` and `worker` containers (`docker compose stop api worker`) before running
the integration tests again.
