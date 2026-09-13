# Architecture

Updated as subsystems land. Sections describe what exists, not what is planned —
the plan lives in [roadmap.md](roadmap.md).

## Shape

A RAG knowledge service with two runtime components sharing one database:

- **API** — accepts uploads and questions, serves answers with citations
- **Worker** — ingests uploaded documents asynchronously

State lives in PostgreSQL; original documents live in object storage. The worker ingests
uploads, and the API answers questions from what it indexed.

## Current state

End of M4. Uploads are stored, then parsed, chunked, and embedded by the worker.
`POST /questions` searches a collection's chunks for a question by meaning and by its
words, answers from the best-ranked with cited passages, and records every question with
its outcome, timings, and the search that served it. An evaluation harness scores
retrieval and answers against a frozen corpus and golden set, and compares dense with
hybrid search on every run.

**API** — FastAPI, built by a factory rather than a module-level app so tests can
construct one with their own settings. Routes:

| Endpoint | Behaviour |
| --- | --- |
| `GET /health/live` | Process liveness. Checks nothing external, deliberately. |
| `GET /health/ready` | Fails with 503 when the database is unreachable. |
| `POST /collections` | Creates a collection. 409 on a duplicate name. |
| `GET /collections` | Keyset pagination with an opaque cursor. |
| `POST /documents` | Uploads to object storage, inserts document and job in one transaction, returns 202. |
| `POST /documents/{document_id}/reindex` | Queues a stored document for ingestion again and returns 202 with the new job, whose completion replaces the document's chunks. 409 while one of its jobs is queued or running, 404 unless the document is the caller's. |
| `GET /jobs/{job_id}` | A job's status, attempts, error, and timestamps. 404 unless it is in the caller's collections. |
| `POST /questions` | Answers from one collection with cited passages and per-stage timings, recording every question. 201 for an answer or insufficient evidence, 502 when generation declines or fails, 404 unless the collection is the caller's. |

**Authentication** — every route but the health checks requires an `X-API-Key` header
matching `API_KEY`, compared in constant time. The check is attached where routers are
included rather than route by route, so a route added to a protected router cannot miss
it. A missing and a wrong key get the same 401. The key maps every request to the seeded
user, and the API refuses to start without one; the worker and the evaluation harness
serve no HTTP and never read it. One static key is enough to prove the service enforces
authentication; a table of hashed keys, which a second tenant would need, is parked.

**Ownership** — routes find a caller's collections and jobs by id through the lookups in
`app/db/ownership.py`, each filtering on the caller's user id, and listing collections
filters on it too. Another user's resource therefore comes back exactly as a missing one
does, a 404 with the same body, so a response never confirms that someone else's id is
real. Search filters by collection alone, once a lookup has established ownership:
`chunks` carries no user id, and a join would defeat its indexes.

**Logging** — the API and the worker write one JSON object per line to stdout: time,
level, logger, message, any fields passed with the record, and a traceback where there is
one. `app/logs.py` supplies the formatter, and each process configures logging once, in
its entrypoint; the API runs uvicorn from `python -m app.main` with uvicorn's own logging
configuration and access log turned off. A pure ASGI middleware gives every request an id,
the caller's `X-Request-ID` when it is 1–128 letters, digits, `.`, `_`, or `-` and a new one
otherwise, returns it as a response header, and holds it in a context variable the
formatter adds to every line written while the request runs. When the request finishes,
the middleware logs one line with its method, route template, status, and duration, and a
request that raises is logged as a 500 with its traceback. That 500 is sent by the server
outside the middleware, so it carries no `X-Request-ID` header. No line carries a header
or a body, so the API key and question text never reach the logs. An upload logs the job
it queued, which leads from a request id to the worker's lines about that job.

**Unavailable database** — a request that cannot reach the database gets 503 with
`Retry-After: 5` and `{"detail": "database unavailable"}`, rather than a 500, so a client
can tell an outage to wait out from a fault. SQLAlchemy raises `OperationalError` for an
unreachable database and for failed queries alike, so `app/api/errors.py` answers 503 only
when the error has no SQLSTATE (the connection failed before any server answered), is a
class 08 connection exception, or is 57P01–57P03 (the server shutting down, crashed, or
not yet accepting connections), and when the connection pool times out. Deadlocks,
serialization failures, cancelled queries, and every other database error stay 500s. Each
503 is logged as a warning with its request id. A question whose row cannot be recorded
after the model has answered gets the 503 too, and its answer is lost: the service never
returns an answer it has not recorded. Object storage outages are still 500s.

**Data** — PostgreSQL 16 with pgvector 0.8.6. Compose and CI pin its image by version
and digest: the `pg16` tag moves with each release, and index-scan options depend on the
extension version. Seven tables: `users`, `collections`,
`documents`, `chunks`, `ingestion_jobs`, `questions`, `retrieval_results`. Schema is
managed by Alembic and applies from empty. `chunks.embedding` is `vector(384)`, fixed
by the embedding model; `chunks.collection_id` is denormalised from `documents` so
tenant-filtered vector search stays single-table. `chunks.tsv` holds each chunk's
full-text lexemes, a stored column the database generates from its text, so every path
that writes chunks fills it. `questions.outcome` is `answered`,
`insufficient_evidence`, `declined`, or `failed`, enforced by a check constraint, so
every question asked can be counted. Declined and failed rows keep the exception's class
name in `error_class`, never its message, and `invalid_citations` is null wherever no
answer was checked. `questions.retriever` is `dense` or `hybrid`, also enforced by a check
constraint: a recorded retrieval score is an inner product under one and a fused score
under the other. `retrieval_results.chunk_id` is set to null when its chunk is deleted, as
reindexing a document deletes its chunks, so a past question keeps each result's rank,
score, and whether it was cited, losing only which chunk it was.

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
alone, so a container can migrate its own database and only one image ever does. Both
images carry the embedding weights, since the worker embeds passages and the API embeds
questions. The weights are fetched at build time, and both images run with the Hugging
Face Hub offline, so a missing model fails at startup rather than downloading. Compose runs the API, the worker, PostgreSQL, and MinIO with dependency
ordering, and health checks on everything but the worker, which serves no HTTP.

**Verification** — unit tests with no I/O, and integration tests against real
PostgreSQL, MinIO, and the embedding model that skip when those are unavailable. CI
runs lint, type checking, migrations, and the suite on every push and pull request,
with the same pgvector image Compose uses and the model's weights cached between runs.
Those runs answer with the stub generator. Tests against the real Claude API are marked
`live` and excluded unless selected; a separate workflow runs them on pushes to `main`
that change more than documentation, and on manual dispatch, failing outright if its
API key secret is missing rather than skipping. After the tests, CI runs the evaluation
harness with the stub generator and writes its retrieval figures to the run's summary.

**Ingestion** — the worker turns uploads into embedded chunks; see
[Ingestion](#ingestion).

**Retrieval** — dense, full-text, and hybrid search over one collection's chunks, chosen
by `RETRIEVER`; see
[Retrieval](#retrieval).

**Answer generation** — a generator protocol, numbered context, Claude and stub backends
selected by `GENERATOR`, and citation validation; see
[Answer generation](#answer-generation).

**Questions** — `POST /questions` ties search, generation, and citation checks together;
see [Answering a question](#answering-a-question).

**Evaluation** — a harness in `eval/` scores retrieval and answers against a frozen
corpus and golden set; see [Evaluation](#evaluation).

**Not yet built** — Kubernetes manifests and AWS infrastructure.

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

**Reindexing.** `POST /documents/{document_id}/reindex` queues a new job for a document
already stored, to retry a failed ingestion or re-embed after the chunker or the model
changes. It locks the document's row before looking for a job in flight, so of two
concurrent requests one queues and the other waits, then gets 409. The job runs as any
other, reading the stored object, which is keyed by content hash and never deleted, and
its final transaction deletes the document's existing chunks before writing the new ones:
search sees the old chunks or the new, never both or neither. Past questions' retrieval
results survive with a null chunk id. A new job rather than a reset one keeps every
attempt's record, so an earlier job id still reports what happened to it. Reindexing a
whole collection is one call per document.

**Fencing.** `attempts` doubles as a fencing token. Heartbeat, completion, and failure
all require the attempt a worker claimed with, so a worker that stalls past the
staleness window and is reclaimed finds its claim gone at its next heartbeat and
discards its work. Completion is checked first in the final transaction and locks the
job row; the unique `(document_id, chunk_index)` constraint backs it up.

**Failure.** Every failure is terminal. The job is marked `failed` with a reason — a
parse error's own message, which tells the uploader what is wrong with the file, or
`unexpected error during ingestion` for anything else — and nothing is retried
automatically. An unexpected exception's type, message, and traceback go only to the
worker's log, with the job id: they can carry storage or library detail that
`GET /jobs/{job_id}`, which returns the recorded reason, should not show a caller. Only a worker that dies outright has its job retried,
through reclaim, for up to three attempts. If the database is unreachable when a failure
is recorded, the job stays `running` and is reclaimed as after a crash. The cost is
that a transient storage error fails a document until it is re-uploaded or reindexed.

**Shutdown.** SIGTERM and SIGINT set a flag the loop checks between jobs, so a worker
finishes the job in hand before exiting. On a laptop CPU a 30-page document embeds in
under three seconds and a 300-page one in about thirty.

## Retrieval

`RETRIEVER` chooses how a question's five chunks are found: `hybrid`, the default, or
`dense`. Both stay runnable, and `app/retrieval/search.py` is the one place the choice is
made, for the service and the evaluation harness alike. Every search reads one
collection's chunks, and every result carries what a citation points at.

**Dense search** returns the chunks of one collection nearest a question's embedding.
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

**Full-text search** finds the chunks holding a question's words. The question is stemmed
and stripped of stopwords by the `english` configuration the chunks were indexed with, and
a chunk matches if it holds any of the words that remain: `plainto_tsquery` alone would
require all of them, which the passage answering a question rarely has. Matches are ranked
by `ts_rank`, ties by chunk id, and found through a GIN index on `tsv`. A question of
stopwords alone matches nothing.

**Hybrid search** runs both for 50 candidates each, in one transaction, and fuses their
rankings by reciprocal rank fusion: a chunk scores the sum, over the lists it appears in,
of 1 / (60 + its rank). The searches' own scores have unrelated scales and are never
combined, so a hybrid result's score is its fused score, at most 2/61. Candidates are
gathered to the same depth whatever the result limit, so the top five are always the
first five of the top ten, and ties go to the lower chunk id. With nothing matched by
words, the dense ranking stands.

Hybrid became the default in M4, when it retrieved the evidence for two more golden-set
questions than dense search did and ranked none lower; see
[evaluation.md](evaluation.md#results).

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

## Answering a question

`POST /questions` runs a question through four timed stages, then checks citations. The
API process embeds the question itself, having loaded the model at startup; searches the
collection with the strategy `RETRIEVER` names; numbers the chunks found into passages;
and asks the generator. The
search's transaction is committed before the generator is called, so a generation that
takes seconds never holds a pooled database connection. The generator is built at
startup too, so a missing API key stops the API before it serves anything.

With nothing retrieved, the model is not called and the outcome is insufficient
evidence. Otherwise the outcome is `answered` if at least one statement survives citation
checks, and insufficient evidence if none does or the model itself judged the passages
inadequate; both return 201. A safety refusal is `declined` and any other generation
error `failed`. Both are recorded with the stages that ran, the tokens reported, and the
exception's class name, and the caller gets a 502 that says nothing of the cause.

Every question writes a `questions` row, naming the retriever that searched, and one
`retrieval_results` row per retrieved chunk, with its rank, score, and whether the
returned answer cites it. Timings are
returned in the response and stored on the row: `embed_ms`, `search_ms`, `prep_ms`,
`llm_ms`, and `total_ms`, which covers everything but the write that records them.

## Evaluation

The harness in `eval/` runs beside the service rather than inside it, against the same
database, and exercises the service's own code: `parse_pdf`, `chunk_pages`, and the
embedder to index the corpus, `search` with `RETRIEVER` to retrieve, and `answer_question` to
answer. A run verifies six NASA reports against their SHA-256 manifest, validates the
golden set's structure and every evidence quote against the parsed text, and replaces the
eval user's collection with a fresh index of the corpus. It then scores Recall@5 and
MRR@10, runs the pre-registered comparisons of dense with hybrid search and of the query
prefix, and puts every question through
the answering path, scoring refusal and citation validity from the rows
`answer_question` records rather than from what it returns. `make eval` answers with the
stub and leaves answering unmeasured; `make eval-live` answers with Claude. See
[evaluation.md](evaluation.md) for how to run it, how it is scored, and the results.

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

Added as each subsystem is built: Kubernetes, AWS.
