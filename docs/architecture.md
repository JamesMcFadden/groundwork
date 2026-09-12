# Architecture

Updated as subsystems land. Sections describe what exists, not what is planned —
the plan lives in [roadmap.md](roadmap.md).

## Shape

A RAG knowledge service with two runtime components sharing one database:

- **API** — accepts uploads and questions, serves answers with citations
- **Worker** — ingests uploaded documents asynchronously

State lives in PostgreSQL; original documents live in object storage. The worker ingests
uploads; the API does not yet answer questions.

## Current state

End of M1. Uploads are stored, then parsed, chunked, and embedded by the worker;
nothing searches the chunks yet.

**API** — FastAPI, built by a factory rather than a module-level app so tests can
construct one with their own settings. Routes:

| Endpoint | Behaviour |
| --- | --- |
| `GET /health/live` | Process liveness. Checks nothing external, deliberately. |
| `GET /health/ready` | Fails with 503 when the database is unreachable. |
| `POST /collections` | Creates a collection. 409 on a duplicate name. |
| `GET /collections` | Keyset pagination with an opaque cursor. |
| `POST /documents` | Uploads to object storage, inserts document and job in one transaction, returns 202. |
| `GET /jobs/{job_id}` | A job's status, attempts, error, and timestamps. 404 unless it is in the caller's collections. |

**Data** — PostgreSQL 16 with pgvector. Seven tables: `users`, `collections`,
`documents`, `chunks`, `ingestion_jobs`, `questions`, `retrieval_results`. Schema is
managed by Alembic and applies from empty. `chunks.embedding` is `vector(384)`, fixed
by the embedding model; `chunks.collection_id` is denormalised from `documents` so
tenant-filtered vector search stays single-table.

**Object storage** — S3 API, MinIO locally. Keys are the SHA-256 of the content, so
uploading the same file twice writes one object. One code path serves both
environments; only the endpoint differs. MinIO no longer publishes images, so Compose
and CI run one built from its last community source release by
[docker/minio/Dockerfile](../docker/minio/Dockerfile), published to GitHub's container
registry and pinned by digest.

**Configuration** — `pydantic-settings`, from the environment or a local `.env`.
Secrets are declared without defaults, so a misconfigured deployment fails at startup
rather than falling back to a weak credential.

**Packaging** — a multi-stage Dockerfile with `api` and `worker` targets: dependencies
install into a virtualenv in a builder stage, which a shared slim non-root runtime stage
copies, so the two images share one environment. Migrations ship in the api image
alone, so a container can migrate its own database and only one image ever does. The
worker image also carries the embedding weights, fetched at build time, and runs with
the Hugging Face Hub offline, so a missing model fails at startup rather than
downloading. Compose runs the API, the worker, PostgreSQL, and MinIO with dependency
ordering, and health checks on everything but the worker, which serves no HTTP.

**Verification** — unit tests with no I/O, and integration tests against real
PostgreSQL, MinIO, and the embedding model that skip when those are unavailable. CI
runs lint, type checking, migrations, and the suite on every push and pull request,
with the same pgvector image Compose uses and the model's weights cached between runs.

**Ingestion** — the worker turns uploads into embedded chunks; see
[Ingestion](#ingestion).

**Not yet built** — retrieval, answer generation, Kubernetes manifests, and AWS
infrastructure.

## Ingestion

The worker polls `ingestion_jobs` once a second. Each pass first fails any job that has
been abandoned with no attempts left, then claims the oldest available job in a single
`UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED)`: queued, or running with a
heartbeat more than five minutes stale. Both queries read a partial index on
`created_at` that holds only queued and running jobs, so they stay fast however many
jobs have finished. The claim increments `attempts` and commits before any work begins,
so every attempt is counted even if the worker dies. While there is work, the queue
drains without waiting between jobs.

A job then runs in four steps:

1. **Fetch** the PDF from object storage by its content-hash key.
2. **Parse** it to per-page text with pymupdf. Pages without text are kept so page
   numbers stay true; a file that will not open, or holds no text at all, fails.
3. **Chunk** into windows of at most 510 tokens with at least 64 of overlap, running
   across page breaks and cut only between words. Tokens are the embedding model's
   own, counted by an untruncated copy of its tokenizer, and each chunk records its
   first and last page.
4. **Embed** with bge-small-en-v1.5 through fastembed, 32 chunks at a time, with a
   heartbeat before each batch.

Chunks, `page_count`, and the job's completion commit in one transaction, so a document
is indexed fully or not at all and a retry never has partial output to clean up.

**Fencing.** `attempts` doubles as a fencing token. Heartbeat, completion, and failure
all require the attempt a worker claimed with, so a worker that stalls past the
staleness window and is reclaimed finds its claim gone at its next heartbeat and
discards its work. Completion is checked first in the final transaction and locks the
job row; the unique `(document_id, chunk_index)` constraint backs it up.

**Failure.** Every failure is terminal. The job is marked `failed` with a reason — a
parse error's own message, or the exception's type and message for anything else — and
nothing is retried automatically. Only a worker that dies outright has its job retried,
through reclaim, for up to three attempts. If the database is unreachable when a failure
is recorded, the job stays `running` and is reclaimed as after a crash. The cost is
that a transient storage error fails a document until it is re-uploaded or reindexed.

**Shutdown.** SIGTERM and SIGINT set a flag the loop checks between jobs, so a worker
finishes the job in hand before exiting. On a laptop CPU a 30-page document embeds in
under three seconds and a 300-page one in about thirty.

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

Added as each subsystem is built: retrieval, answer generation and citations,
evaluation, Kubernetes, AWS.
