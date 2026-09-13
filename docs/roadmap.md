# Roadmap

Lightweight backlog. Detail is added for the current milestone only; future milestones
stay one line until they are next.

**Now:** M6 — Kubernetes on kind
**Branching:** M0 lands on `main`; from M1 each milestone gets a branch and a
CI-gated PR.
**Next item:** M6 — start branch `m6-kubernetes`
**Budget:** ~52.5h total, range 44–60h. M0, M1, M2, M3, and M4 took their estimated 11h,
7h, 9.5h, 4h, and 4h.

## Milestones

Hours are estimates from planning, not commitments. They exist to keep scope
proportionate: a milestone running far over is a signal to cut, not to continue.

- [x] **M0** (11h) Foundations + document API — tooling, compose, Dockerfile, schema, upload
- [x] **M1** (7h) Async ingestion — worker image, skip-locked queue, parse/chunk/embed
- [x] **M2** (9.5h) RAG query path — vector search, Claude call, cited answers
- [x] **M3** (4h) Eval harness — golden set (30 answerable + 8 unanswerable), Recall@5,
      citation validity, refusal
- [x] **M4** (4h) Hybrid retrieval — FTS + RRF, dense-vs-hybrid ablation
- [x] **M5** (2h) Hardening — API key, query scoping, logging, 503, reindex
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

Merged through PR #4 as `4549aa2`.

Planned 2026-09-12.

- [x] `feat(eval): add frozen corpus with checksum manifest`
- [x] `feat(eval): add golden set format with validation`
- [x] `feat(eval): add golden set of 30 answerable and 8 unanswerable questions`
- [x] `feat(eval): ingest the corpus into an isolated eval collection`
- [x] `feat(eval): report Recall@5 and MRR@10 for dense retrieval`
- [x] `feat(eval): report refusal and citation validity through the answering path`
- [x] `ci: run the retrieval eval on every pull request`
- [x] `feat(eval): compare retrieval with and without a query instruction prefix`
- [ ] `feat(embeddings): embed queries with the bge instruction prefix` — not needed: the
      prefix rule kept questions unprefixed (see the results below)
- [x] `docs: add evaluation doc and record M3 results`

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
- **The live run is local**, as `make eval-live`, estimated at $1–2 per full run of 38
  questions at `claude-opus-5` list prices. Planned as `GENERATOR=anthropic` and changed
  while building: the service's settings default to `anthropic`, so a shell with a key
  exported would have spent money on every `make eval`. The harness takes
  `--answers stub|claude` instead, defaulting to the stub. The first live run used
  181,862 input and 7,176 output tokens, about $1.09.

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

Results, recorded 2026-09-12 from a run of commit `2047afb`
(`eval/results/20260912T165717Z.json`); detail in
[evaluation.md](evaluation.md#results):

- **Recall@5 27/30 = 0.900 (n=30)**, exactly on the target, so one question fewer would
  miss it. MRR@10 0.763. Unanswerable questions refused 8/8, with 3/30 false refusals,
  which are the three questions retrieval missed. No returned citation named a chunk
  outside those cited, and the model cited no passage it was not given (0/49).
- **Query prefix: not adopted.** With the instruction, Recall@5 fell to 26/30 and MRR@10
  rose to 0.786. It fixed no question and broke `a22`, a net −1 against the +2 the rule
  required, so item 9 is not needed.
- **Relevance threshold: none.** Unanswerable questions' best chunk scores (0.724–0.871,
  median 0.779) sit inside the answerable range (0.663–0.873, median 0.764). A cutoff
  above every unanswerable score would refuse 29 of 30 answerable questions; one at the
  lowest unanswerable score would refuse 7 answerable questions and no unanswerable ones.
  The model's own judgement refused all 8.

## M4 — Hybrid retrieval

Merged through PR #5 as `bf0db1f`.

Planned 2026-09-12.

- [x] `feat(db): add generated full-text column and gin index to chunks`
- [x] `feat(retrieval): add collection-filtered full-text search`
- [x] `feat(retrieval): fuse dense and full-text results with reciprocal rank fusion`
- [x] `feat(db): record the retriever that served each question`
- [x] `feat(retrieval): select dense or hybrid search with RETRIEVER`
- [x] `feat(eval): search through the configured retriever and record it`
- [x] `feat(eval): compare dense and hybrid retrieval`
- [x] `feat(retrieval): default RETRIEVER to hybrid`, only if the adoption rule below says
      to adopt it
- [x] `docs: record M4 results`

Hybrid search runs dense search and full-text search over the same collection and fuses
their rankings. Dense search matches meaning, but compresses a 510-token chunk into one
vector, blurring the exact terms in it: acronyms, designations, numbers. Full-text search
matches exact stemmed words, but knows no synonyms. Each misses what the other finds.

The baseline is M3's dense search: Recall@5 27/30, exactly on the target, and MRR@10
0.763, missing `a23` (evidence ranked 7th), `a28` (10th), and `a30` (outside the top
10). Hybrid can fix at most three questions, and breaking one more than it fixes misses
the target.

Decisions taken while planning:

- **The full-text column is generated by the database.** `chunks.tsv` is
  `tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED`, with a GIN index,
  in a self-contained migration. The eval harness writes chunks directly rather than
  through `app/ingest/pipeline.py`, so a column the pipeline computed would be empty in
  every eval run, and the comparison would measure hybrid search with no full-text index.
  A generated column fills both paths, and a test asserts that chunks from each carry it.
  Naming the configuration makes `to_tsvector` immutable, as a generated column requires;
  search names the same one.
- **A question matches chunks holding any of its words, not all of them.**
  `plainto_tsquery` stems a question, drops its stopwords, and joins the rest with `&`,
  so a chunk must contain every word. A question rarely shares all its words with the
  passage that answers it, so the full-text list would often be empty and hybrid would
  be dense search in disguise. Search uses the same query with `&` replaced by `|`,
  ranked by `ts_rank` within the collection, ties broken by chunk id.
- **Reciprocal rank fusion with k = 60, over 50 candidates from each search.** A chunk
  scores the sum, over the lists it appears in, of 1 / (60 + its rank). The searches'
  own scores are not combined: inner products and `ts_rank` values have unrelated
  scales. k = 60 is the value from Cormack, Clarke, and Büttcher (2009), taken as given
  rather than tuned on the golden set. Each list is cut to 50 candidates whatever the
  result limit, so a hybrid search's top five are always the first five of its top ten.
  Fusion is a pure function with unit tests, and both searches run in one transaction. A
  question of stopwords alone finds nothing by full text, and hybrid returns dense
  search's order.
- **`questions.retriever` records the strategy that served each question**, `dense` or
  `hybrid`, enforced by a check constraint. `retrieval_results.score` is an inner product
  under dense search and a fused score, around 0.03, under hybrid, so without the column
  recorded scores could not be read. Existing rows are backfilled as `dense`, the only
  strategy there has been.
- **`RETRIEVER=dense|hybrid` defaults to `dense`** until the adoption rule decides. The
  API reads it at startup, and `answer_question` runs the search it selects.
- **The harness searches through `RETRIEVER`**, the selection the service makes, and
  records the strategy in each run's metadata and summary, where `eval/results.py` now
  writes `dense` unconditionally. It needs no flag of its own, unlike `--answers`:
  retrieval costs nothing, so a stray environment setting spends no money, and the
  record names what ran. The query-prefix comparison stays on dense search, as a closed
  M3 result. Best chunk scores are reported from dense search only: a fused score is
  built from ranks and says nothing about a relevance threshold.
- **The comparison runs on every eval**: dense and hybrid over one ingested collection
  and the same 30 answerable questions, reported as questions fixed and broken at 5
  beside each strategy's Recall@5 and MRR@10, as the prefix comparison is. CI shows it
  on every pull request.
- **A live run only if hybrid is adopted.** One `make eval-live`, about $1, so refusal
  and false-refusal figures describe the strategy the service serves. If dense stays
  the default, M3's live figures still describe it.

Pre-registered rule, fixed before any hybrid result exists:

- **Adoption.** `RETRIEVER` defaults to `hybrid` only if, over the 30 answerable
  questions, hybrid fixes at least two more Recall@5 hits than it breaks and MRR@10 does
  not fall: the bar the query prefix faced. With three misses, that means fixing two
  and breaking none, or fixing all three and breaking one. Otherwise the default stays
  `dense`. Either way both strategies stay runnable and the comparison is recorded.

Checked 2026-09-12 against PostgreSQL 16.15 in the pinned image, on made-up passages
and questions rather than the golden set:

- **The generated column is accepted** with `to_tsvector('english', text)`, and fills on
  insert.
- **All-words matching fails on partial overlap.** "How loud was the boom people heard
  on the ground?" became `'loud' & 'boom' & 'peopl' & 'heard' & 'ground'` and matched
  none of three passages, though one held three of those five words. Joined with `|`, it
  matched two, that one ranked first.
- **Replacing `&` with `|` leaves a valid query.** Hyphenated words come out as the
  compound and its parts joined by `&` (`'sleep-wak' & 'sleep' & 'wake'`), not by a
  phrase operator, so every operator is replaced.
- **A question of stopwords alone gives an empty query**, with a notice, and matches
  nothing.
- **Designations split.** "X-59" is indexed as two lexemes, `'x'` and `'-59'`.

Results, recorded 2026-09-12; detail in [evaluation.md](evaluation.md#results):

- **Hybrid adopted.** The comparison, run by `make eval` at `38dd661`
  (`eval/results/20260912T220646Z.json`), scored hybrid Recall@5 29/30 = 0.967 against
  dense's 27/30, and MRR@10 0.892 against 0.763. It fixed `a23` and `a28` and broke none:
  net +2, exactly the bar, so `feat(retrieval): default RETRIEVER to hybrid` followed.
  `a30` is still missed, now ranked 6th. CI's Linux runner reproduced the comparison
  exactly.
- **Answers through hybrid**, by `make eval-live` at `e2ad10b`
  (`eval/results/20260912T221514Z.json`): unanswerable questions refused 8/8, false
  refusals 1/30 (`a30`) against dense's 3/30, and 0 of 54 citations invalid. Search took
  15 / 20 ms at P50 / P95, against dense's 6 / 8.
- **One CI failure, not a regression.** On `f930220`, a fusion test's premise that dense
  search leaves a chunk out of its top five failed: HNSW is approximate, and CI's `chunks`
  table is heavily churned by the time that test runs. `dd4bb58` runs the dense searches
  in the fusion tests that assert an exact order without an index scan.

## M5 — Hardening

Merged through PR #6 as `754af27`.

Planned 2026-09-12. Review added two items to the milestone's original five: the
migration reindex needs to keep past questions' retrieval results, and a generic reason
for unexpected ingestion failures, the decision carried to this milestone.

- [x] `feat(auth): add api key check mapped to the seeded user`
- [x] `feat(auth): scope queries by user id`
- [x] `feat(obs): add json logging with request id propagation`
- [x] `feat(ingest): record a generic reason for unexpected ingestion failures`
- [x] `feat(api): return 503 when the database is unavailable`
- [x] `feat(db): keep retrieval results when their chunk is deleted`
- [x] `feat(api): add document reindex endpoint`

The milestone's line was written in M0, before most of what it hardens existed. Every
route that reads by id already checks ownership, so query scoping is now consolidation
rather than new behaviour. Reindex touches two things the line did not foresee: retrying
a failed upload, which architecture.md already offers, and the retrieval results of past
questions, which replacing a document's chunks would delete.

Decisions taken while planning:

- **One static key, mapped to the seeded user.** `API_KEY` is a secret setting, sent as
  the `X-API-Key` header and compared with `hmac.compare_digest`. A missing and a wrong
  key get the same 401. The check is attached to every router but health's where routers
  are included, so a new route cannot forget it and probes need no key. Like
  `ANTHROPIC_API_KEY`, it is optional in settings and required when the API starts, so
  the worker and the eval harness, which serve no HTTP, run without it. A table of hashed
  keys, needed for a second tenant, stays parked.
- **Ownership is checked by one set of lookups.** Uploads, questions, and jobs each write
  their own ownership query; they are replaced by lookups that take the caller's user id,
  which reindex then uses. Another user's collection, document, or job is a 404 exactly
  like a missing one, and uploads, the one route taking an id without a test of that,
  gain one. Listing collections gains a test that another user's are left out, which it
  lacks too. Search stays filtered by collection alone: `chunks` carries no user id, a
  join would defeat its indexes, and the lookup has already established ownership.
- **Logs are JSON lines from the standard library**, so no new dependency: one formatter
  and one `configure_logging()` shared by the API and the worker. A pure ASGI middleware
  takes the request id from `X-Request-ID` when it is 1–128 letters, digits, `.`, `_`, or
  `-`, and otherwise generates one. It holds the id in a context variable that a logging
  filter adds to every record, and returns it as a response header. Uvicorn's access log
  is turned off in favour of one line per request: method, route, status, duration, and
  request id. An upload logs the job it queued, so a request id leads to that job's
  worker logs without a column to carry it. The API key, question text, and answer text
  are never logged.
- **Unexpected ingestion failures record a generic reason.** `GET /jobs/{job_id}` returns
  `ingestion_jobs.error` as stored, and for an unexpected failure the worker stores the
  exception's type and message, which can carry storage error text. It will store
  `unexpected error during ingestion` instead, and log the exception with its traceback
  and job id. A parse failure keeps its message, which tells the uploader what is wrong
  with the file, and an abandoned job keeps its reason. The same reasoning keeps
  `questions.error_class` to a class name. Rows failed before M5 keep their text; there is
  no deployed data to rewrite. The change is the worker's, so the item is scoped `ingest`
  rather than `api` as first drafted.
- **503 means the database cannot be reached, not that a query failed.** SQLAlchemy
  raises `OperationalError` for an unreachable database, and also for deadlocks,
  serialization failures, and cancelled queries, which are not outages. A handler returns
  503 with `Retry-After: 5` only when the underlying error has no SQLSTATE (the connection
  failed before the server answered), is a class 08 connection exception, or is 57P01–
  57P03 (the server shutting down, crashed, or not yet accepting connections), and for
  SQLAlchemy's pool `TimeoutError`, raised when every pooled connection stays busy. Any
  other database error stays a 500. A question whose row cannot be written after the model
  answered gets the 503 too, and its answer is lost: the service never returns an answer
  it has not recorded. Object storage outages stay 500s, outside this item, and failing
  readiness during shutdown belongs to M6.
- **Retrieval results outlive their chunks.** `retrieval_results.chunk_id` becomes
  nullable, with `ON DELETE SET NULL` in place of `CASCADE`. Reindex replaces a document's
  chunks, and under `CASCADE` would delete the retrieval results of every past question
  that retrieved them. They now keep their question, rank, score, and whether they were
  cited, losing only which chunk. Deleting a collection still removes its questions and
  their results through `questions.collection_id`. The downgrade deletes results whose
  chunk is gone before restoring `NOT NULL`. A test asserts that deleting a chunk leaves
  its result with a null chunk id.
- **Reindex queues a new job for a stored document.**
  `POST /documents/{document_id}/reindex` returns 202 with the document, the new job, and
  its status, as an upload does: 404 unless the document is the caller's, 409 while one
  of its jobs is queued or running.
  The request locks the document's row before looking for such a job, so of two
  concurrent reindexes one queues and the other gets 409, with no new index. A new job
  rather than a reset one keeps every attempt's record, so an earlier job id still
  reports what happened to it. The worker reads the stored object again, which is keyed
  by content hash and never deleted. On completion it deletes the document's existing
  chunks in the transaction that writes the new ones and marks the job done, so search
  sees the old chunks or the new, never both or neither. A completed document reindexes to
  re-embed, a failed one to retry. Reindexing a whole collection is not built: after a
  model change it is one call per document.

Checked 2026-09-12 against the pinned PostgreSQL image, with the SQLAlchemy and psycopg
versions in `uv.lock`:

- **Unavailability arrives as `OperationalError`, and so do other failures.** With
  nothing listening, the engine raised `sqlalchemy.exc.OperationalError` wrapping
  `psycopg.OperationalError`, with no SQLSTATE. A backend terminated under an open
  connection raised it wrapping `AdminShutdown`, SQLSTATE 57P01, with the connection
  invalidated. psycopg's `DeadlockDetected` (40P01), `SerializationFailure` (40001), and
  `QueryCanceled` (57014) subclass `OperationalError` too. SQLAlchemy's pool
  `TimeoutError` does not.
- **A context variable set by middleware reaches everything a request runs.** Set in a
  pure ASGI middleware, it was visible in a synchronous route, in a yield dependency's
  body and its exception branch, and in an exception handler returning 503.
- **The unique constraint accepts repeated nulls.** PostgreSQL 16 treats nulls as
  distinct by default, so `uq_retrieval_question_chunk` can hold several results with no
  chunk for one question.
- **The eval harness reads no chunk ids from `retrieval_results`.** It scores citations
  from `rank` and `cited` alone.

## M6 — Kubernetes on kind

Planned 2026-09-13. Checking what the plan rests on added one item to the nine first
agreed: ONNX Runtime sizes its threads from the node rather than the container, so the
CPU limits the scaling comparison needs would throttle query embedding from about 7 ms
to 104 ms at P50.

- [ ] `feat(worker): add a liveness heartbeat for its probe`
- [ ] `feat(embeddings): size onnx runtime threads with EMBEDDING_THREADS`
- [ ] `build: add kind cluster config with a pinned node image`
- [ ] `feat(k8s): add postgres and minio with persistent volumes`
- [ ] `feat(k8s): run migrations and bucket creation as jobs`
- [ ] `feat(k8s): add api and worker deployments with probes and resources`
- [ ] `feat(k8s): take api pods out of rotation before they stop`
- [ ] `feat(load): add k6 question load test and corpus seeding`
- [ ] `docs: record M6 scaling and recovery results`
- [ ] `docs: add kubernetes doc`

The milestone fills three rows of [success-criteria.md](success-criteria.md), API
reliability, scaling, and recovery, all measured by k6 on kind, and the latency row,
whose timings the load runs record. Five decisions were carried to it; each is settled
below.

Decisions taken while planning:

- **One kind node, Kubernetes 1.34.** `k8s/kind-cluster.yaml` pins
  `kindest/node:v1.34.11` by digest. kind v0.33.0 defaults to 1.37, but the `kubectl`
  Docker Desktop ships is 1.34.1, which supports one minor version of skew. Every kind
  node is a container on the same Docker VM, so more nodes would add scheduling
  boundaries and no capacity. kind is installed from its release binary with the
  checksum verified, not through a package manager, so the documented steps name one
  version.
- **Plain manifests under `k8s/`, applied with `kubectl apply -k`.** Kustomize ships in
  kubectl, so there is no new tool. Whether M7 needs a base and overlays is M7's
  decision.
- **Data services run in the cluster**, from the images Compose and CI pin: PostgreSQL
  and MinIO each as a one-replica StatefulSet with a volume on kind's default `standard`
  class, behind a Service named as in Compose, so `POSTGRES_HOST` and `S3_ENDPOINT` take
  the values Compose gives them. Deleting the cluster deletes their data.
- **Secrets come from a gitignored file.** A Kustomize `secretGenerator` reads
  `k8s/secrets.env`, holding `POSTGRES_PASSWORD`, `S3_SECRET_KEY`, and `API_KEY`, with
  `secrets.env.example` committed beside it. Applying without the file fails, and the
  Secret's generated name changes with its content, so a new value rolls the pods that
  read it. PostgreSQL and MinIO take their passwords from the same Secret. The cluster
  sets `GENERATOR=stub` and holds no `ANTHROPIC_API_KEY`: load tests measure this
  service, not a model provider.
- **Application images are built locally and loaded into kind**, tagged with the commit
  they were built from and run with `imagePullPolicy: Never`, so an image that was never
  loaded fails to start rather than being looked for on Docker Hub. They are not pinned
  by digest: an image never pushed has no registry digest, and its tag names its commit.
  Every image pulled from a registry, the node, PostgreSQL, MinIO, and k6, is pinned by
  digest.
- **Migrations and the bucket are Jobs**, run from the api image as Compose's
  `createbucket` is. An init container would run `alembic upgrade` in every API pod at
  once. The documented steps wait for both Jobs to complete before the API takes load.
- **One uvicorn process per API pod, with 1 CPU requested and limited**, as carried:
  without limits one replica could use every CPU on the node, and the 1→3 comparison
  would measure contention rather than scaling. The worker gets the same. Memory requests
  and limits are set in that commit from usage measured under load; an idle API
  container used 270 MiB.
- **`EMBEDDING_THREADS` sizes ONNX Runtime's thread pools**, and the cluster sets it to
  each pod's CPU limit. Unset, fastembed leaves ONNX Runtime to size them from the node's
  10 CPUs, which a 1-CPU limit then throttles: query embedding took 104 ms at P50 and
  300 ms at P95, against 6.9 and 7.6 ms with one thread. Left unset elsewhere, it changes
  nothing for Compose, CI, or the eval harness.
- **The API's probes use the endpoints it has.** A startup probe on `/health/live`
  covers loading the model, the liveness probe reads the same path, and the readiness
  probe `/health/ready`.
- **The worker's liveness is a file it touches.** The loop touches `/tmp/worker-alive` at
  the start of every pass, and ingestion at every job heartbeat. An `exec` probe fails
  when the file is more than 120 seconds old, read with `stat` and `date`, both in the
  image. The threshold must exceed the longest gap between touches, which that commit
  measures on a 300-page document. Both candidates carried here were rejected:
  `heartbeat_at` changes only while a job runs, so an idle worker would look wedged, and
  a probe that reads the database would restart every worker during a database outage,
  the failure `/health/live` is written to avoid. A probe-only HTTP endpoint would still
  have to check the loop's freshness, which the file does without a server.
- **An API pod drains before it is told to stop.** Its `preStop` hook runs
  `sh -c 'kill -USR1 1; sleep 10'`. SIGUSR1 marks the API as draining, and from then on
  every response carries `Connection: close`, so each client's next request opens a new
  connection, which kube-proxy routes to another pod. The sleep holds off SIGTERM while
  the pod's removal from the Service's endpoints reaches kube-proxy and open connections
  close, so when uvicorn stops it has no idle connection to close under a client. The
  default 30-second grace period covers the sleep and uvicorn finishing its requests. The
  carried decision proposed failing readiness, but deleting a pod already marks its
  endpoint terminating and not ready: failing readiness would reach kube-proxy no sooner,
  and would do nothing for connections already open. A signal starts draining rather
  than an HTTP hook because the health routes take no API key, so a drain endpoint would
  let anyone who reaches the Service take pods out of rotation. The API runs as PID 1,
  and `kill` is built into `sh`. A test asserts that responses carry `Connection: close`
  once draining starts.
- **k6 runs inside the cluster**, as a Job from `grafana/k6:2.2.0` pinned by digest,
  against the API's ClusterIP Service: `kubectl port-forward` sends every request to one
  pod, which would make three replicas indistinguishable from one. The script sends
  `X-API-Key` from the Secret. Each virtual user keeps its connection, as a real client
  would, so kube-proxy balances connections rather than requests; every run reports how
  many requests each API pod served, from the pods' request logs.
- **The corpus is seeded through the API.** A script in `load/` creates a collection,
  uploads the six eval corpus PDFs so the worker in the cluster ingests them, waits for
  their jobs, and writes the collection id and the golden set's 38 questions, read from
  `eval/golden.toml` unchanged, into the ConfigMap the k6 script reads. It reaches the
  API through `kubectl port-forward`, where one pod is enough. Workers are then scaled to
  0 for every load run, as carried, so ingestion never competes for CPU.
- **Pool waits are checked before errors are attributed**, as carried. Each API process
  has SQLAlchemy's default pool, 5 connections and 10 overflow with a 30-second wait, and
  a pool timeout is a 503. Every run reports its 503s and the API's logged 503 warnings,
  and a run with any is read against pool waits before its errors or throughput are put
  down to the service or the replica count.
- **Results are committed** under `load/results/`, one JSON file per run: k6's summary
  with the commit, replica count, node image, k6 version, and requests served per pod.

Pre-registered rules, fixed before any load run:

- **Every run.** 30 virtual users post golden-set questions in turn, with no pause
  between requests, for 5 minutes, after a 1-minute warm-up run at 30 users whose figures
  are discarded. Every API pod is ready, workers are at 0, and the seeded collection is
  the same.
- **Scaling.** Six runs, alternating 1 and 3 API replicas and starting with 1, so drift
  across the session falls on both. Throughput is completed requests per second, and P95
  is k6's `http_req_duration` P95 over the run. Met if the median throughput of the
  3-replica runs is at least 1.8 times that of the 1-replica runs, and their median P95
  is no higher.
- **API reliability.** Met if each of the six runs has at least 99% of requests answered
  with a status other than 5xx; a request with no response counts against it. The worst
  run is the figure reported.
- **Latency.** The P95 of `total_ms − llm_ms` over the `questions` rows recorded during
  the three 3-replica runs, met below 500 ms, with the 1-replica runs' figure reported
  beside it. The timings are taken inside the service, so they leave out time a request
  waits before its handler runs, which k6's P95 includes.
- **Recovery.** Three 5-minute runs at 3 replicas, each after a warm-up. Two minutes into
  each, the API pod first in name order is deleted with `kubectl delete pod` and its
  default grace period. Met if all three record no failed request, a failure being any
  status other than 201 or a request with no response.
- **Wiring checks** before the measured runs use a few users for seconds, are never
  recorded, and are read for status codes only, so no throughput or latency figure is
  seen before these rules apply.
- **A miss is recorded, not retuned.** A change made after a miss, such as a longer
  `preStop` sleep, is recorded as a new result beside the miss, not in its place.

Checked 2026-09-13 on this machine, an Apple Silicon Mac running Docker Desktop with 10
CPUs and 8 GB for containers:

- **kind v0.33.0** was released 2026-08-26, and its repository is active and not
  archived. Its `kind-darwin-arm64` binary matched the published SHA-256, `0c8c7dbe…`.
  The release notes list pre-built node images for 1.37.0, 1.36.4, 1.35.8, and 1.34.11,
  to be used by digest. A cluster from `v1.34.11` reported server version 1.34.11, 10
  CPUs and 8,024,852 KiB of memory allocatable, and a default `standard` StorageClass
  (`rancher.io/local-path`, binding when a pod first uses a volume).
- **The 1.34 API accepts a `preStop` sleep.** `kubectl explain` lists
  `lifecycle.preStop.sleep.seconds`. That shows the field exists, not that it prevents
  lost requests; the recovery runs show that.
- **k6 v2.2.0** was released 2026-08-10 with no breaking changes, and its repository is
  active and not archived. `grafana/k6:2.2.0` is a multi-platform index,
  `sha256:9bd01d6941fca969cb61bb57d2da5ee9b385fe2aa8881df3798c196564d6ace6`, with amd64
  and arm64 images.
- **uvicorn 0.52.4 closes idle keep-alive connections at once on shutdown.** It stops
  listening, closes every connection with no request in flight, marks in-flight responses
  `connection: close`, and by default waits for them without a timeout. A client that
  sends a request on an idle connection as it closes gets no response, and a POST cannot
  safely be retried. This is the main risk to the recovery criterion, and a `preStop`
  sleep alone does not remove it: kube-proxy balances new connections, and one already
  open keeps reaching its pod after the pod leaves the endpoints. A `connection: close`
  header the application sets makes uvicorn close that connection once the response is
  sent, which draining relies on. On Unix the server handles only SIGINT and SIGTERM;
  SIGUSR1 appears only in uvicorn's gunicorn worker class, which this service does not
  run.
- **ONNX Runtime ignores a container's CPU limit.** fastembed sets its thread counts only
  when given `threads`. In the api image, with and without `--cpus=1`, `nproc` reported
  10 and the started API ran 25 threads. Timing 300 query embeddings after 20 warm-up
  calls, one caller at a time: 3.8 / 5.9 ms at P50 / P95 with no limit and default
  threads, 7.1 / 7.6 ms with no limit and one thread, 104.1 / 300.1 ms (at most 600 ms)
  under `--cpus=1` with default threads, and 6.9 / 7.6 ms under `--cpus=1` with one.
- **An idle API container uses about 270 MiB** once the model has loaded, with the stub
  generator, with or without a CPU limit (n=1 each). Three replicas, the worker,
  PostgreSQL, MinIO, k6, and the control plane must share about 7.65 GiB.
- **The runtime image has `stat`, `date`, and `sleep`**, so the worker's probe needs
  nothing added to it.

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
- **Retrieval — `RETRIEVER=dense|hybrid`, hybrid by default.** Both strategies stay
  runnable for the life of the project, so the ablation table is reproducible rather than
  remembered, and every eval run compares them. Hybrid became the default in M4 under the
  rule pre-registered for it; see the M4 results.
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

- **M7** — timebox EKS to one day. If the cluster is not serving traffic by then, ship
  the Terraform, the eksctl config, and the runbook, and say so plainly in the README.
- **M7** — serve the API over HTTPS before its LoadBalancer takes traffic. Since M5 every
  request carries the API key in the `X-API-Key` header, which plain HTTP exposes to
  anyone on the path, and a key read in transit admits its reader as the seeded user and
  spends the service's Claude budget. Terminating TLS at the load balancer with an ACM
  certificate is the likely route; ACM validates a certificate against a domain, so one is
  needed, not just the load balancer's generated hostname. Kind in M6 is reached on
  localhost, where plain HTTP exposes nothing.
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
