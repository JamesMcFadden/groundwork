# Architecture

Updated as subsystems land. Sections describe what exists, not what is planned —
the plan lives in [roadmap.md](roadmap.md).

## Shape

A RAG knowledge service with two runtime components sharing one database:

- **API** — accepts uploads and questions, serves answers with citations
- **Worker** — ingests uploaded documents asynchronously

State lives in PostgreSQL; original documents live in object storage. The API exists;
the worker does not yet.

## Current state

End of M0. The API accepts documents and stores them; nothing indexes them yet.

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

**Packaging** — a multi-stage Dockerfile with an `api` target: dependencies install
into a virtualenv in a builder stage, which a slim non-root runtime copies. Migrations
ship in the image so a container can migrate its own database. Compose runs the API,
PostgreSQL, and MinIO with health checks and dependency ordering.

**Verification** — 26 tests, split between unit tests with no I/O and integration tests
against real PostgreSQL and MinIO. CI runs lint, type checking, migrations, and the
suite on every push and pull request, with the same pgvector image Compose uses.

**Not yet built** — the ingestion worker, chunking, embeddings, retrieval, answer
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
