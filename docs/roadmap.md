# Roadmap

Lightweight backlog. Detail is added for the current milestone only; future milestones
stay one line until they are next.

**Now:** M1 — Async ingestion
**Branching:** M0 lands on `main`; from M1 each milestone gets a branch and a
CI-gated PR.
**Next item:** M1 — review and merge PR #1; every M1 item is in
**Budget:** ~51h total, range 44–60h. M0 took its estimated 11h.

## Milestones

Hours are estimates from planning, not commitments. They exist to keep scope
proportionate: a milestone running far over is a signal to cut, not to continue.

- [x] **M0** (11h) Foundations + document API — tooling, compose, Dockerfile, schema, upload
- [ ] **M1** (7h) Async ingestion — worker image, skip-locked queue, parse/chunk/embed
- [ ] **M2** (8h) RAG query path — vector search, Claude call, cited answers
- [ ] **M3** (4h) Eval harness — golden set (30 answerable + 8 unanswerable), Recall@5,
      citation validity, refusal
- [ ] **M4** (4h) Hybrid retrieval — FTS + RRF, dense-vs-hybrid ablation
- [ ] **M5** (2h) Hardening — API key, query scoping, logging, 503, reindex
- [ ] **M6** (6h) Kubernetes on kind — manifests, probes, scaling and pod-kill evidence
- [ ] **M7** (6h) AWS — Terraform (S3/ECR/RDS), eksctl + IRSA, deploy and tear down
- [ ] **M8** (3h) CI/CD + write-up — ECR push, README results, runbook

M3 and M4 are what distinguish this from an LLM demo. If time runs short, cut M7 to
a single deploy-and-teardown; do not cut the evaluation milestones.

## M0 — Foundations + document API

- [x] `chore: init uv project with ruff, mypy, pytest`
- [x] `chore: pin vscode interpreter to project venv`
- [x] `docs: add success criteria and target metrics`
- [x] `build: add docker compose with postgres/pgvector and minio`
- [x] `feat(api): add app factory with live and ready health endpoints`
- [x] `build: add multi-stage Dockerfile with api target`
- [x] `feat(db): add schema for collections, documents, chunks, and jobs`
- [x] `feat(db): add initial alembic migration`
- [x] `feat(api): add collections create and list with keyset pagination`
- [x] `fix(config): require secrets instead of defaulting them`
- [x] `feat(storage): add s3 client with minio-compatible config`
- [x] `feat(api): add POST /documents with atomic document and job insert`
- [x] `ci: run lint, type check, and tests on pull requests`
- [x] `docs(adr): use postgres skip-locked queue instead of redis`
- [x] `docs(adr): use eksctl for cluster, terraform for data services`

## M1 — Async ingestion

- [x] `feat(worker): add worker entrypoint and container`
- [x] `feat(db): add skip-locked job claim with heartbeat reclaim`
- [x] `feat(ingest): parse pdf to per-page text with pymupdf`
- [x] `feat(ingest): add page-aware token chunking with overlap`
- [x] `feat(embeddings): add embedder protocol with fastembed backend`
- [x] `feat(worker): wire ingestion pipeline and job state transitions`
- [x] `feat(api): add GET /jobs/{job_id}`
- [x] `test(integration): assert concurrent workers never double-claim`
- [x] `test(integration): assert stalled jobs are reclaimed`

## Stack decisions

Chosen during planning, with the reasoning that is not recoverable from the code.
Change them deliberately, not incidentally.

- **Embeddings — `fastembed` with `bge-small-en-v1.5`, 384 dimensions.** Chosen over
  `sentence-transformers` because it runs ONNX without torch, which keeps images smaller
  and makes CI embeddings free and deterministic. As built in M1 (arm64), the api image
  is 755 MB and the worker 883 MB, 407 MB of each the shared virtualenv. Planning had
  estimated 400 MB against ~2.5 GB with torch; neither figure was measured. The
  dimension is baked into `chunks.embedding`, so changing model means a migration and a
  full re-embed — which is what the reindex endpoint exists for. fastembed serves
  Qdrant's quantized ONNX export, about 63 MB of weights. Its tokenizer truncates at
  512, so the chunker counts with an untruncated copy rather than the original.
- **PDF parsing — `pymupdf`.** Fast, good layout handling, per-page text. AGPL: fine
  for this repository, worth knowing. Scanned and OCR documents are out of scope.
- **Chunking — 510 tokens with 64 overlap, running across page breaks.** Overlap means
  a sentence crossing a boundary appears whole in at least one chunk. A page break is
  layout rather than meaning, so windows cross it, and chunks record `page_start` and
  `page_end` for that reason. Tokens are the embedding model's own, supplied by the
  embeddings backend: bge-small-en-v1.5 takes 512 including two special tokens and
  silently truncates beyond that, hence 510. Chunk text is sliced from the source by
  character offsets, never decoded from token ids, and window edges fall only between
  words: the model re-tokenizes chunk text, and cutting mid-word pushed real chunks to
  511 tokens. Revised in M1 from "512, scoped to a page", which contradicted both the
  page columns and the model's window.
- **Generation — `claude-opus-5`.** Citations come back as structured output via
  `client.messages.parse()` with a Pydantic model; assistant prefills return 400 on
  Opus 5, so no prefill. Thinking is on by default and `max_tokens` caps thinking plus
  answer, so use `output_config={"effort": "low"}` with generous `max_tokens` rather
  than disabling thinking. Handle `stop_reason == "refusal"` before reading content.
- **Retrieval — `RETRIEVER=dense|hybrid` config flag.** Both strategies stay runnable
  for the life of the project, so the ablation table is reproducible rather than
  remembered.

## Carried decisions

Decisions taken ahead of their milestone, recorded so they are not lost. Do not
implement early; apply when the milestone is reached. Rationale in
[success-criteria.md](success-criteria.md).

- **M1** — add the `worker` target to the Dockerfile alongside `app/worker.py`. M0
  builds only the `api` target; a worker image with no worker module to run would be
  scaffolding for code that does not exist.
- **M2** — create the HNSW index on `chunks.embedding` here, not earlier: indexes
  belong with the queries that need them, and an index built over zero rows tells you
  nothing. Note that a `collection_id` predicate is not used by the HNSW index, so
  filtered search over-fetches and post-filters, tuning `hnsw.ef_search`.
- **M2** — chunk ids never go to the model. The prompt numbers its context `[1]`–`[5]`
  per request and the server maps those back to chunk ids. Small integers cost fewer
  tokens and are cited more reliably than UUIDs, and the mapping is what makes citation
  validation deterministic.
- **M2** — add a `GENERATOR=stub` flag selecting the fake answer generator, so load
  tests measure this service rather than the LLM provider.
- **M2** — bge vectors come back unit-length, so cosine distance and inner product
  rank identically; choose the HNSW operator class knowing that. fastembed embeds
  queries exactly as it embeds passages for this model, so whether a query instruction
  prefix helps is an M3 measurement rather than an assumption.
- **M2** — move the baked embedding weights from the `worker` stage into the shared
  `runtime` stage once the API embeds queries. M1 bakes them into the worker alone,
  since the API had nothing to load them for.
- **M4** — add the `tsv` column and its GIN index here. `chunks` deliberately has no
  full-text column yet: nothing references it, so it is a self-contained migration, and
  a GIN index over zero rows is meaningless.
- **M5** — decide how much of a job's recorded error `GET /jobs/{job_id}` returns. For
  unexpected failures the worker records the exception's type and message, which can
  carry internal detail such as storage error text; callers may warrant a generic
  reason, with the detail kept in the logs.
- **M6** — scale workers to 0 and set explicit CPU requests/limits during load runs, or
  the 1→3 replica comparison measures contention rather than scaling.
- **M6** — the worker needs a liveness probe of its own. The API's probe is an HTTP
  GET; the worker has no HTTP surface, so that pattern does not transfer. Two
  candidates: an `exec` probe reading `heartbeat_at` freshness from the database, or
  a minimal HTTP endpoint on the worker serving probes only. Decide there — a probe
  that only checks the process is alive would pass for a worker wedged mid-job, which
  is worse than no probe at all.
- **M7** — timebox EKS to one day. If the cluster is not serving traffic by then, ship
  the Terraform, the eksctl config, and the runbook, and say so plainly in the README.

## Parked

Ideas deliberately out of scope for now. Do not start these; they exist so good ideas
have somewhere to go that is not the current branch.

- HPA + metrics-server
- Hashed API-key table for real multi-tenancy
- LLM judge for answer groundedness
- k6 load matrix run against EKS (currently kind only)
- Redis response cache
- Cross-encoder reranking after retrieval — cut for time; hybrid carries M4
- A `make check` target running all four verification commands as one
- Automatic retry with backoff for transient ingestion failures. Needs a retry-after
  column and a claim predicate; requeueing without backoff spends every attempt in
  seconds during an outage, so it is not worth doing halfway.
