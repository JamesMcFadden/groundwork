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

M2 in progress. Uploads are stored, then parsed, chunked, and embedded by the worker,
and a collection's chunks can be searched by similarity to a question; nothing answers
questions yet.

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

**Data** — PostgreSQL 16 with pgvector 0.8.6. Compose and CI pin its image by version
and digest: the `pg16` tag moves with each release, and index-scan options depend on the
extension version. Seven tables: `users`, `collections`,
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

**Retrieval** — dense search over one collection's chunks; see
[Retrieval](#retrieval).

**Answer generation** — a generator protocol, numbered context, Claude and stub backends
selected by `GENERATOR`, and citation validation; see
[Answer generation](#answer-generation).

**Not yet built** — `POST /questions`, Kubernetes manifests, and AWS infrastructure.

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

## Retrieval

Dense search returns the five chunks of one collection nearest a question's embedding.
Similarity is the inner product, pgvector's `<#>`: bge-small-en-v1.5 vectors are
unit-length, so it equals cosine similarity and ranks chunks the same way. Scores are
reported as similarities, higher meaning closer.

The nearest chunks are chosen from `chunks` alone, filtered on its denormalised
`collection_id`, and only those five are then joined to `documents` for their filenames.
Each result carries what a citation points at: chunk, document, filename, pages, and
score. Equal scores are ordered by chunk id, so the same question retrieves the same
chunks in the same order.

An HNSW index on `chunks.embedding`, built with `vector_ip_ops` to match the `<#>`
ordering, lets the planner find the nearest chunks without comparing the question with
every one. The index knows nothing of collections: it yields candidates from all of them
and the filter discards the rest, so by default a small collection in a large index can
lose every candidate and come back short. Search therefore enables pgvector's iterative
index scan for its own transaction (`hnsw.iterative_scan = relaxed_order`), which keeps
reading candidates until enough match, and re-sorts the relaxed order it returns. While
a collection is small, the planner may still prefer the `collection_id` b-tree and an
exact sort, which returns the same answer.

## Answer generation

A generator takes a question and numbered passages and answers in statements, each
citing the passage numbers it relies on, together with its own judgement of whether the
passages answer the question at all. Structured statements, rather than prose with
inline markers, mean citations never have to be parsed out of text.

Retrieved chunks are numbered from 1 in retrieval order for each request. The passages a
generator receives carry a number, text, filename, and pages, but no chunk or document
id: ids never reach the model, and a cited number maps back to its chunk by position.
Filenames are escaped where the context is laid out, since they come from uploads.

The stub generator answers with the opening words of the top passage and cites it. It
makes no network call and gives the same answer every time, so CI needs no API key and
load tests measure this service rather than a model provider.

The Claude backend asks `claude-opus-5` for JSON matching a schema of statements and
cited numbers, at low effort with thinking left on. It reads the stop reason before any
content: a safety refusal raises `GenerationDeclined` and a response cut short raises
`GenerationIncomplete`, each keeping the tokens it used, while API errors and timeouts
propagate under the SDK's own names. `GENERATOR` selects the backend and defaults to
`anthropic`. Selecting it without `ANTHROPIC_API_KEY` fails when the generator is built,
and the stub answers only when named, never as a fallback.

Citations are checked before an answer is returned or recorded. A cited number is valid
only if it names a passage supplied for that request. Invalid numbers are dropped and
counted once each, and a statement left citing nothing is dropped whole, since every
claim must rest on a passage. Repeated numbers collapse to one citation. What survives
is rendered as prose with `[n]` markers placed before each statement's closing
punctuation.

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

Added as each subsystem is built: evaluation, Kubernetes, AWS.
