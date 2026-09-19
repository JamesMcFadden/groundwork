# Groundwork

A production-style RAG knowledge service. Users upload documents, the system indexes
them asynchronously, and questions are answered from retrieved source material with
citations back to the originating document and page.

**Status:** complete. Every criterion in
[docs/success-criteria.md](docs/success-criteria.md) has been measured against targets
fixed before anything was built.

## Results

Each figure carries the conditions that produced it. The full table, the scoring rules,
and what is deliberately not measured are in
[docs/success-criteria.md](docs/success-criteria.md).

| What | Result |
| --- | --- |
| Retrieval | Recall@5 **0.967** (29/30, hybrid) against a 0.90 target |
| Citations | **0** invalid citations in 54 returned markers (n=38, `claude-opus-5`) |
| Refusal | **8/8** unanswerable questions refused, with 1/30 false refusals |
| API reliability | **100%** non-5xx over 140,828 requests, 30 VU × 5 min, stubbed generator |
| Latency | **P95 243 ms** for `POST /questions` over 94,095 questions, excluding the model |
| Ingestion | **4.05 s** for a 30-page PDF (n=1) |
| Scaling | **2.12×** median throughput from 1→3 API replicas, P95 no worse |
| Recovery | **0** failed requests when an API pod is deleted under load (n=3 runs) |
| AWS | Served over TLS on EKS, answering against RDS, every smoke check passed |
| Deployment | **39.3 s** from one `docker compose up` to a ready API, from a cold clone |

The numbers measure retrieval quality **on this corpus**: the golden set is 30 answerable
and 8 unanswerable questions over 6 documents, written from the source PDFs rather than
from the chunks, and frozen since. At n=30 the interval around a 0.90 recall is wide, so
no claim is made about retrieval in general. Load, scaling, and recovery were measured on
kind with a stubbed answer generator, which keeps them about this service rather than a
model provider's throughput; AWS proves the deployment path. Answer groundedness is
enforced structurally, since citations must resolve to supplied chunks, but is not scored
semantically — that needs a calibrated LLM judge, which was cut for time.

The retrieval ablation behind the hybrid default, and the run that chose it, are in
[docs/evaluation.md](docs/evaluation.md#results).

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

Requires [uv](https://docs.astral.sh/uv/), Python 3.12, and Docker. The walkthrough under
[Running the service](#running-the-service) also reads responses with
[jq](https://jqlang.github.io/jq/), a system binary rather than a Python package:
`brew install jq`, or your platform's equivalent.

```bash
cp .env.example .env        # placeholder secrets work locally; change them for anything shared
uv sync                     # install dependencies
```

### Checks

```bash
uv run ruff check .         # lint
uv run ruff format --check .
uv run mypy .               # type check
uv run pytest               # tests
```

Unit tests always run. Integration tests need PostgreSQL, MinIO, and the embedding
model, and skip with a message when any of them is unavailable. To run them:

```bash
docker compose up -d postgres minio createbucket   # data services and the bucket
uv run alembic upgrade head                        # schema
uv run pytest
```

The first run downloads the embedding model (about 63 MB). Set `EMBEDDING_CACHE_DIR` to
keep it somewhere that survives restarts.

Start only these services for tests. A running `worker` container claims the jobs the
integration tests create, and they fail unpredictably.

Tests that call the real Claude API are excluded from every run unless selected, since
each run costs money. CI runs them on pushes to `main` and on demand; to run them
locally, export a key first:

```bash
ANTHROPIC_API_KEY=... uv run pytest -m live tests/live
```

### Evaluation

```bash
make eval        # retrieval figures and the query-prefix comparison; free, and what CI runs
make eval-live   # also measures answers with Claude on ANTHROPIC_API_KEY, about $1–2 a run
```

Both need PostgreSQL with migrations applied. What each figure means, and the latest
results, are in [docs/evaluation.md](docs/evaluation.md).

### Running the service

`.env.example` sets `GENERATOR=stub`, so the service comes up and answers with no paid
key: answers are the opening words of the top retrieved passage. For answers from Claude,
put a key in `ANTHROPIC_API_KEY` and comment `GENERATOR` out. The code's own default is
`anthropic`, which refuses to start without a key rather than serve fake answers, so a
deployment that sets neither fails at startup rather than quietly stubbing.

Every route but `/health` needs the `X-API-Key` header to match `API_KEY` in `.env`, and
the API refuses to start without one.

```bash
# applies migrations, creates the bucket, and waits until the api is healthy
docker compose up -d --build --wait

# API_KEY from .env
KEY=$(grep '^API_KEY=' .env | cut -d= -f2)

# check connectivity
curl -s localhost:8000/health/ready

# create a collection, keeping its id for the commands below
CID=$(curl -s -X POST localhost:8000/collections -H "x-api-key: $KEY" \
  -H 'content-type: application/json' -d '{"name": "demo"}' | jq -r .id)

# upload (general command) -> 202 {"document_id", "job_id", "status"}
curl -s -X POST localhost:8000/documents -H "x-api-key: $KEY" \
  -F collection_id=<collection id> -F file=@report.pdf

# upload the demo pdfs, keeping every job id
JOBS=$(for f in eval/corpus/*.pdf; do
  curl -s -X POST localhost:8000/documents -H "x-api-key: $KEY" \
    -F collection_id=$CID -F file=@"$f" | jq -r .job_id
done)

# poll one job (general command): queued, running, then completed or failed
curl -s localhost:8000/jobs/<job id> -H "x-api-key: $KEY"

# wait for every upload: a question asked before this answers from an empty index
for job in $JOBS; do
  while :; do
    status=$(curl -s localhost:8000/jobs/$job -H "x-api-key: $KEY" | jq -r .status)
    case $status in completed|failed) echo "$job $status"; break ;; esac
    sleep 2
  done
done

# queues a stored document for ingestion again (general command), after a failed one
curl -s -X POST localhost:8000/documents/<document id>/reindex -H "x-api-key: $KEY"

# ask (general command) -> 201 with answer, citations, per-stage timings
curl -s -X POST localhost:8000/questions -H "x-api-key: $KEY" -H 'content-type: application/json' \
  -d '{"collection_id": "<collection id>", "question": "What does the report conclude?"}'
```

#### Asking the golden set

`eval/ask_api.py` does that whole walk in one command, against a running API. It creates a
collection, uploads the six NASA reports from `eval/corpus/`, waits for every ingestion
job, then asks the golden set's questions and prints what came back:

```bash
KEY=$KEY uv run python -m eval.ask_api --upload --limit 3
```

```
a01  201  answered                cited-evidence-document=yes  small-satellite-failure-rates.pdf
u01  201  insufficient_evidence   refused=yes                  -
```

| Flag | Effect |
| --- | --- |
| `--upload` | create a collection, upload the corpus, wait for indexing, then ask |
| `--collection <id>` | ask against a collection that already holds the corpus, such as the `$CID` above |
| `--limit N` | ask only the first N answerable and N unanswerable questions |
| `--dry-run` | list the questions and ask nothing; needs no key and no service |
| `--base <url>` | an API other than `http://localhost:8000` |

`KEY` is the shell variable above; the `KEY=` prefix passes it to the process. Every
question is charged unless `GENERATOR=stub`, so `--limit` is worth using first.

This is a walkthrough, not a measurement: the API returns the passages an answer cited,
not the chunks behind them, so a citation naming the right report is far coarser than the
harness's Recall@5. The figures to cite come from `make eval`; see
[docs/evaluation.md](docs/evaluation.md).

Stop the `api` and `worker` containers (`docker compose stop api worker`) before running
the integration tests again.

To run the service on a local Kubernetes cluster, and load test it there, see
[docs/kubernetes.md](docs/kubernetes.md). To deploy it on AWS, check it with the smoke test,
and tear it down, see [docs/aws.md](docs/aws.md). When something is wrong wherever it runs,
[docs/runbook.md](docs/runbook.md) is arranged by symptom.
