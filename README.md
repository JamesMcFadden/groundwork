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

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync                     # install dependencies
uv run ruff check .         # lint
uv run ruff format --check .
uv run mypy .               # type check
uv run pytest               # tests
```
