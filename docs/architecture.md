# Architecture

Updated as subsystems land. Sections describe what exists, not what is planned —
the plan lives in [roadmap.md](roadmap.md).

## Shape

A RAG knowledge service with two runtime components sharing one database:

- **API** — accepts uploads and questions, serves answers with citations
- **Worker** — ingests uploaded documents asynchronously

State lives in PostgreSQL; original documents live in object storage. Both components
exist; the worker does not yet ingest anything.

## Current state

End of M0, with M1 under way. The API accepts documents and stores them; nothing
indexes them yet.

**API** — FastAPI, built by a factory rather than a module-level app so tests can
construct one with their own settings. Routes:

| Endpoint | Behaviour |
| --- | --- |
| `GET /health/live` | Process liveness. Checks nothing external, deliberately. |
| `GET /health/ready` | Fails with 503 when the database is unreachable. |
| `POST /collections` | Creates a collection. 409 on a duplicate name. |
| `GET /collections` | Keyset pagination with an opaque cursor. |
| `POST /documents` | Uploads to object storage, inserts document and job in one transaction, returns 202. |

**Data** — PostgreSQL 16 with pgvector. Seven tables: `users`, `collections`,
`documents`, `chunks`, `ingestion_jobs`, `questions`, `retrieval_results`. Schema is
managed by Alembic and applies from empty. `chunks.embedding` is `vector(384)`, fixed
by the embedding model; `chunks.collection_id` is denormalised from `documents` so
tenant-filtered vector search stays single-table.

**Object storage** — S3 API, MinIO locally. Keys are the SHA-256 of the content, so
uploading the same file twice writes one object. One code path serves both
environments; only the endpoint differs.

**Configuration** — `pydantic-settings`, from the environment or a local `.env`.
Secrets are declared without defaults, so a misconfigured deployment fails at startup
rather than falling back to a weak credential.

**Packaging** — a multi-stage Dockerfile with `api` and `worker` targets: dependencies
install into a virtualenv in a builder stage, which a shared slim non-root runtime stage
copies, so the two images differ only in entrypoint. Migrations ship in the api image
alone, so a container can migrate its own database and only one image ever does.
Compose runs the API, the worker, PostgreSQL, and MinIO with dependency ordering, and
health checks on everything but the worker, which serves no HTTP.

**Verification** — unit tests with no I/O, and integration tests against real
PostgreSQL and MinIO that skip when the stack is down. CI runs lint, type checking, migrations, and the
suite on every push and pull request, with the same pgvector image Compose uses.

**Ingestion — in progress (M1)** — a worker process runs beside the API on the same
configuration and database, and stops cleanly on SIGTERM, but does not yet process
jobs. The pieces it will run exist as tested components, not yet wired together:

- a single-statement `FOR UPDATE SKIP LOCKED` claim, which also reclaims jobs whose
  heartbeat has gone stale, bounded by an attempt count;
- per-page PDF text extraction with pymupdf, keeping empty pages so page numbers stay
  true;
- chunking into 510-token windows with 64 tokens of overlap that run across page
  breaks, recording the first and last page of each chunk.

**Not yet built** — embeddings, the wired ingestion pipeline, retrieval, answer
generation, Kubernetes manifests, and AWS infrastructure.

## Decisions

### Job queue in PostgreSQL rather than Redis

The `ingestion_jobs` table is the queue, claimed with `FOR UPDATE SKIP LOCKED`. A
document and its job commit in one transaction, so there is no dual-write problem and
no separate broker to run.

See [ADR 0001](adr/0001-postgres-skip-locked-queue.md).

### eksctl for the cluster, Terraform for data services

Terraform manages the resources that hold state — S3, ECR, RDS. The cluster and its
IRSA service account are created with `eksctl`, which is created and destroyed per
demo and therefore not worth expressing by hand.

See [ADR 0002](adr/0002-eksctl-for-cluster-terraform-for-data.md).

## Sections to be written

Added as each subsystem is built: ingestion pipeline, retrieval, answer generation and
citations, evaluation, Kubernetes, AWS.
