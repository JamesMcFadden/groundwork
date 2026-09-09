# Roadmap

Lightweight backlog. Detail is added for the current milestone only; future milestones
stay one line until they are next.

**Now:** M1 — Async ingestion
**Branching:** M0 lands on `main`; from M1 each milestone gets a branch and a
CI-gated PR.
**Next item:** M1 — `feat(ingest): parse pdf to per-page text with pymupdf`
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
- [ ] `feat(ingest): parse pdf to per-page text with pymupdf`
- [ ] `feat(ingest): add page-aware token chunking with overlap`
- [ ] `feat(embeddings): add embedder protocol with fastembed backend`
- [ ] `feat(worker): wire ingestion pipeline and job state transitions`
- [ ] `feat(api): add GET /jobs/{job_id}`
- [ ] `test(integration): assert concurrent workers never double-claim`
- [ ] `test(integration): assert stalled jobs are reclaimed`

## Stack decisions

Chosen during planning, with the reasoning that is not recoverable from the code.
Change them deliberately, not incidentally.

- **Embeddings — `fastembed` with `bge-small-en-v1.5`, 384 dimensions.** Chosen over
  `sentence-transformers` because it runs ONNX without torch, keeping the image near
  400 MB instead of ~2.5 GB and making CI embeddings free and deterministic. The
  dimension is baked into `chunks.embedding`, so changing model means a migration and a
  full re-embed — which is what the reindex endpoint exists for.
- **PDF parsing — `pymupdf`.** Fast, good layout handling, per-page text. AGPL: fine
  for this repository, worth knowing. Scanned and OCR documents are out of scope.
- **Chunking — 512 tokens with 64 overlap, scoped to a page.** Overlap means a sentence
  crossing a boundary appears whole in at least one chunk. Chunks record `page_start`
  and `page_end` because chunk boundaries and page boundaries do not align.
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
- **M3** — cache fastembed model weights in CI; the ONNX download is ~100 MB per run.
- **M4** — add the `tsv` column and its GIN index here. `chunks` deliberately has no
  full-text column yet: nothing references it, so it is a self-contained migration, and
  a GIN index over zero rows is meaningless.
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
- CI actions target the deprecated Node 20 runtime and are force-upgraded to Node 24.
  `actions/checkout@v5` and a newer `setup-uv` clear the annotation. Cosmetic; fold
  into the next commit that touches the workflow.
