# Roadmap

Lightweight backlog. Detail is added for the current milestone only; future milestones
stay one line until they are next.

**Now:** M3 — Eval harness
**Branching:** M0 lands on `main`; from M1 each milestone gets a branch and a
CI-gated PR.
**Next item:** M3 — start branch `m3-eval-harness`
**Budget:** ~52.5h total, range 44–60h. M0, M1, and M2 took their estimated 11h, 7h, and
9.5h.

## Milestones

Hours are estimates from planning, not commitments. They exist to keep scope
proportionate: a milestone running far over is a signal to cut, not to continue.

- [x] **M0** (11h) Foundations + document API — tooling, compose, Dockerfile, schema, upload
- [x] **M1** (7h) Async ingestion — worker image, skip-locked queue, parse/chunk/embed
- [x] **M2** (9.5h) RAG query path — vector search, Claude call, cited answers
- [ ] **M3** (4h) Eval harness — golden set (30 answerable + 8 unanswerable), Recall@5,
      citation validity, refusal
- [ ] **M4** (4h) Hybrid retrieval — FTS + RRF, dense-vs-hybrid ablation
- [ ] **M5** (2h) Hardening — API key, query scoping, logging, 503, reindex
- [ ] **M6** (6h) Kubernetes on kind — manifests, probes, scaling and pod-kill evidence
- [ ] **M7** (6h) AWS — Terraform (S3/ECR/RDS), eksctl + IRSA, deploy and tear down
- [ ] **M8** (3h) CI/CD + write-up — ECR push, scheduled CI, README results, runbook

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

Merged through PR #1 as `a3aa63f`.

- [x] `feat(worker): add worker entrypoint and container`
- [x] `feat(db): add skip-locked job claim with heartbeat reclaim`
- [x] `feat(ingest): parse pdf to per-page text with pymupdf`
- [x] `feat(ingest): add page-aware token chunking with overlap`
- [x] `feat(embeddings): add embedder protocol with fastembed backend`
- [x] `feat(worker): wire ingestion pipeline and job state transitions`
- [x] `feat(api): add GET /jobs/{job_id}`
- [x] `test(integration): assert concurrent workers never double-claim`
- [x] `test(integration): assert stalled jobs are reclaimed`
- [x] `perf(db): add partial index for the job claim query`

## M2 — RAG query path

Merged through PR #3 as `18c732b`.

Planned 2026-09-11. Budget raised the same day from 8h to 9.5h, matching the estimate
once review added three items: question outcomes, the pgvector pin, and the live-test
workflow. The pin goes first, since search and its index both depend on the extension
version.

- [x] `build: pin the pgvector image to an explicit version`
- [x] `feat(embeddings): add query embedding to the embedder protocol`
- [x] `feat(retrieval): add collection-filtered dense vector search`
- [x] `perf(db): add hnsw index for filtered vector search`
- [x] `feat(generation): add generator protocol, numbered context, and stub backend`
- [x] `feat(generation): add claude backend with structured citations`
- [x] `feat(generation): validate citations against the supplied context`
- [x] `feat(db): record question outcome and invalid citation count`
- [x] `feat(api): add POST /questions with per-stage timings`
- [x] `build: move embedding weights into the shared runtime stage`
- [x] `ci: run the live claude test on main and on demand`
- [x] `test(integration): assert invalid citations are never returned or recorded`
- [x] `test(integration): assert insufficient evidence skips the model call`

Decisions taken while planning:

- **`POST /questions` is synchronous** and returns 201 with the answer, its `[n]`
  markers, and one citation per marker: chunk, document, filename, pages, and score.
  No `GET /questions/{id}` yet; M3 reads the tables directly.
- **Every question is recorded, whatever happens.** `questions.outcome` replaces
  `insufficient_evidence`: `answered`, `insufficient_evidence`, `declined` (a safety
  refusal), or `failed` (a timeout or API error). Declined and failed rows keep the
  timings of the stages that ran, their retrieval results, token counts where reported,
  and the error class name only; callers get a generic 502. M3's rates need every
  question in the denominator, and the table is still empty, so the change costs no data
  migration. Safety refusals never count toward the unanswerable-question criterion.
- **`questions.invalid_citations` counts the cited numbers validation rejected**, the
  raw rate success-criteria.md reports alongside the hard gate.
- **Insufficient evidence skips the model** when retrieval returns nothing, and an
  answer left with no valid citation is downgraded to it. No relevance-score threshold
  until M3 has data to tune one.
- **Search uses inner product on unit-length vectors** through an HNSW index. The
  collection filter applies after the index scan, so search turns on
  `hnsw.iterative_scan` rather than over-fetching with `hnsw.ef_search` as first
  planned; the check below confirmed the pinned version supports it. The pgvector image
  is pinned by version and digest first: `pg16` is a floating tag, and index-scan options
  depend on the extension version.
- **The API process embeds queries**, loading the model at startup, and ends its
  database transaction before calling the model, so a slow generation never holds a
  pooled connection.
- **`GENERATOR=anthropic|stub`**, defaulting to `anthropic` so an unset value never
  serves fake answers. The deterministic stub is what CI and load tests use.
- **The live Claude test runs only on merges to `main` and on manual dispatch**,
  estimated at under $3 a month; every other run uses the stub. Its API key belongs to
  a dedicated Claude Console workspace with a monthly spend limit, is stored as the
  `ANTHROPIC_API_KEY` repository secret, and expires on 2027-12-31. After that date the
  workflow fails with a 401 until a new key replaces it (`gh secret set
  ANTHROPIC_API_KEY`).

Unverified going in; checked 2026-09-11:

- **pgvector supports `hnsw.iterative_scan`.** It arrived in 0.8.0, and `pg16` currently
  resolves to 0.8.6, the latest release. In that image it defaults to `off` and accepts
  `relaxed_order` and `strict_order`, so filtered search need not rely on over-fetching
  alone, but the query must turn it on.
- **`client.messages.parse()` sends a Pydantic format and effort together** in
  `anthropic` 1.5.0, merging the model's schema into `output_config` beside `effort`.
  The effort and structured-output docs both list `claude-opus-5` and neither restricts
  combining them. The backend sends the same merged `output_config` through
  `messages.create()` instead; see the generation stack decision. Confirmed against the
  API by the first live Claude run, on the merge to `main` on 2026-09-12.
- **`usage.output_tokens` includes thinking tokens**, whether thinking is summarized or
  omitted; the docs call it "the inclusive, authoritative total used for billing".
  `usage.output_tokens_details.thinking_tokens` reports the thinking share on its own.

## M3 — Eval harness

Planned 2026-09-12.

- [x] `feat(eval): add frozen corpus with checksum manifest`
- [x] `feat(eval): add golden set format with validation`
- [x] `feat(eval): add golden set of 30 answerable and 8 unanswerable questions`
- [x] `feat(eval): ingest the corpus into an isolated eval collection`
- [ ] `feat(eval): report Recall@5 and MRR@10 for dense retrieval`
- [ ] `feat(eval): report refusal and citation validity through the answering path`
- [ ] `ci: run the retrieval eval on every pull request`
- [ ] `feat(eval): compare retrieval with and without a query instruction prefix`
- [ ] `feat(embeddings): embed queries with the bge instruction prefix`, only if the
      prefix rule below says to adopt it
- [ ] `docs: add evaluation doc and record M3 results`

How each golden-set question is scored is defined in
[success-criteria.md](success-criteria.md#scoring), fixed there before any run.

The corpus is six NASA technical reports from the NASA Technical Reports Server (NTRS),
217 pages and about 67,000 words. They are committed under `eval/corpus/` with a SHA-256
manifest the harness checks before every run. Filenames are what the model sees as each
passage's source.

| File | NTRS ID | Title | Pages |
| --- | --- | --- | --- |
| `small-satellite-failure-rates.pdf` | 20190002705 | Small-Satellite Mission Failure Rates | 46 |
| `faint-lateral-cutoff.pdf` | 20160003115 | Lateral Cutoff Analysis and Results from NASA's Farfield Investigation of No-Boom Thresholds | 46 |
| `x59-cumulative-noise-metrics.pdf` | 20240005399 | Literature Review of Cumulative Noise Metrics Relevant to X-59 Supersonic Overflights | 22 |
| `uam-motion-sickness.pdf` | 20205009977 | Motion Sickness and Concerns for Urban Air Mobility Vehicles: A Literature Review | 40 |
| `uam-risk.pdf` | 20205000604 | Understanding Risk in Urban Air Mobility: Moving Towards Safe Operating Standards | 17 |
| `iss-crew-workload-fatigue.pdf` | 20205006969 | Characterization of International Space Station Crew Members' Workload Contributing to Fatigue, Sleep Disruption and Circadian De-synchronization | 46 |

Two pairs share a topic, so retrieval must choose between documents with the same
vocabulary: FaINT and the X-59 review both concern sonic boom noise metrics, and the two
urban air mobility reports approach air taxis from passenger physiology and from safety
regulation. The ISS fatigue report sits close to the motion sickness review, which gives
near-miss unanswerable questions somewhere to come from.

Checked 2026-09-12, while choosing:

- **Rights.** NTRS records every report as a US government work with public use
  permitted, containing no third-party material and not export controlled.
- **Extraction.** All are born-digital, not scanned. `parse_pdf` output has no
  replacement characters, typographic ligatures, or margin line numbers.
- **Rejected for extraction.** *Lessons Learned from Sonic Boom Flight Research Projects*
  (NTRS 20200011462) is a line-numbered draft whose margin numbers land inside the text
  ("differentiate between 2 requirements"). *A Study in a New Test Facility on Indoor
  Annoyance Caused by Sonic Booms* (20120002057) holds 163 ligatures, which exact quote
  matching would miss; see Parked.

The golden set:

- **Written by a different LLM, from the PDFs alone.** It never sees chunks, retrieval
  results, or answers. Its model and version are recorded with the set, since an
  author's phrasing can favour one retriever or generator over another.
- **Committed before anything is measured.** The questions land before the harness can
  run, so none is adjusted to fit a result. Changing one later needs a dated note in
  success-criteria.md.
- **TOML, read with the standard library's `tomllib`**, so no new dependency:

  ```toml
  [[answerable]]
  id = "a01"
  question = "..."

  [[answerable.evidence]]
  document = "small-satellite-failure-rates.pdf"
  page = 9
  quote = "..."

  [[unanswerable]]
  id = "u01"
  question = "..."
  reason = "..."
  ```

- **Authoring rules**, enforced by validation where they can be:
  - 30 answerable questions, five per report, and 8 unanswerable, at least six of them
    near misses on a report's topic. Each unanswerable question gives a `reason` and is
    checked against all six reports.
  - An answerable question asks for one fact stated in one place. Where the same fact
    also appears elsewhere, further `evidence` entries list it, and any of them counts.
  - `page` is the page's position in the PDF, the first page being 1, not its printed
    number. NASA reports carry front matter, so the two differ.
  - `quote` is verbatim body text from that one page, at most 30 words, and never from a
    table, caption, or running header. Validation requires it to appear in that page's
    parsed text and to be under 48 tokens.
  - Questions are phrased as a reader would ask them, not by rewording the quote:
    borrowing its wording inflates recall.

Decisions taken while planning:

- **The harness is a top-level `eval/` package**, run as `python -m eval` or `make eval`.
  `.dockerignore` already keeps `eval/` out of images. The Makefile holds that one
  target; `make check` stays parked.
- **The harness ingests the corpus itself**, through `parse_pdf`, `chunk_pages`, and
  `embed_passages`, skipping object storage and the job queue: neither affects what
  retrieval finds.
- **Runs are isolated.** The harness owns an eval user, so integration-test fixtures,
  which clear the default user's collections, never touch its data. Each run deletes its
  previous collections first, so the vector index is the same from run to run. Results
  go to `eval/results/` as JSON with run metadata: git commit, corpus and golden-set
  hashes, embedding model, chunk settings, pgvector version, generator, and model. Runs
  cited in documentation are committed.
- **Two layers.** Retrieval metrics need only PostgreSQL and the embedding model, cost
  nothing, and run in CI. Answering metrics put each question through `answer_question`
  and read the `questions` and `retrieval_results` tables; with the stub generator they
  are reported as not measured. CI fails only if the harness does, never on a missed
  target.
- **The live run is local**, with `GENERATOR=anthropic`, estimated at $1–2 per full run
  of 38 questions at `claude-opus-5` list prices. Tokens recorded on each question give
  the actual figure.

Pre-registered rules, fixed before any result exists:

- **Query prefix.** The candidate is bge's retrieval instruction, added to questions
  only, so adopting it needs no re-embedding:
  `Represent this sentence for searching relevant passages: `. Adopt it only if, over
  the 30 answerable questions, it fixes at least two more Recall@5 hits than it breaks
  and MRR@10 does not fall. Otherwise questions stay unprefixed.
- **Relevance threshold.** Record each question's best chunk score, answerable and
  unanswerable alike, and adopt no threshold in M3. Choosing a cutoff from the eight
  unanswerable questions and then scoring refusal on the same eight would grade the
  choice against itself. A threshold waits for held-out questions or real traffic.

## Stack decisions

Chosen during planning, with the reasoning that is not recoverable from the code.
Change them deliberately, not incidentally.

- **Embeddings — `fastembed` with `bge-small-en-v1.5`, 384 dimensions.** Chosen over
  `sentence-transformers` because it runs ONNX without torch, which keeps images smaller
  and makes CI embeddings free and deterministic. As built in M1 (arm64), the api image
  is 755 MB and the worker 883 MB, 407 MB of each the shared virtualenv. In M2 both
  images carry the weights and the `anthropic` SDK, and both are 907 MB: a 408 MB
  virtualenv and 65 MB of weights. Planning had
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
- **Generation — `claude-opus-5`.** Citations come back as structured output: a JSON
  schema derived from a Pydantic model by the SDK's `transform_schema`, requested with
  `client.messages.create()`. Planned as `messages.parse()` and changed in M2: `parse()`
  validates the JSON while it builds the response, before `stop_reason` can be read, so
  a refusal or a truncated answer would raise a validation error instead. Assistant
  prefills return 400 on Opus 5, so no prefill. Thinking is on by default and
  `max_tokens` caps thinking plus answer, so use `output_config={"effort": "low"}` with
  generous `max_tokens` rather than disabling thinking. Handle
  `stop_reason == "refusal"` before reading content.
  Server-side refusal fallbacks stay off: they would route a declined request to another
  model, leaving M3 scoring answers from two models with nothing recording which one
  served each.
- **Retrieval — `RETRIEVER=dense|hybrid` config flag.** Both strategies stay runnable
  for the life of the project, so the ablation table is reproducible rather than
  remembered. The flag arrives with hybrid in M4; until then dense is the only strategy,
  and a flag with one value would be dead config.
- **Local object storage — MinIO, built from source.** MinIO stopped distributing its
  community edition and archived its repository, and the Docker Hub images this project
  pinned were deleted in September 2026. `docker/minio/Dockerfile` builds the last
  community release, `RELEASE.2025-10-15T17-29-55Z`, and a workflow publishes it once to
  `ghcr.io/jamesmcfadden/groundwork-minio`, which Compose and CI pin by digest. The
  package is public, permanently: GitHub never lets a public package go private. It is
  unmaintained: seven 2026 security advisories are fixed only in releases never
  published as source. Acceptable because it stands in for S3 on localhost and CI
  runners and never leaves them; production uses S3.

## Carried decisions

Decisions taken ahead of their milestone, recorded so they are not lost. Do not
implement early; apply when the milestone is reached. Rationale in
[success-criteria.md](success-criteria.md).

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
- **M8** — add a daily scheduled CI run. CI otherwise runs only on pushes and pull
  requests, so breakage from outside the repository waits for the next push: MinIO's
  Docker Hub images vanished between two runs on 2026-09-11 and surfaced only because
  a docs commit happened to follow.
- **M8** — make the live test log list each test by name. `live-claude.yml` runs
  `pytest -v`, but `addopts` in `pyproject.toml` already passes `-q` and the two cancel,
  so passing tests show as dots. Failures are still named in pytest's summary.

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
- Replace MinIO with a maintained S3-compatible server for local development and CI.
  The source-built image works but will never receive fixes.
- Normalise typographic ligatures when parsing. `parse_pdf` keeps characters such as
  "ﬁ" and "ﬀ": the embedding tokenizer reads "coeﬀicients" as `[UNK]`, exact quote
  matching misses them, and M4's full-text search would not match "ﬁrst" to "first". A
  sentence's embedding barely moves (0.989 similarity to its plain form, checked
  2026-09-12), and the M3 corpus contains none. Every chunk would change, so any
  baseline recorded before the fix must be re-run.
