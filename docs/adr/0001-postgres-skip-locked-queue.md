# 1. Use PostgreSQL as the job queue instead of Redis

## Status

Accepted — implemented in M0 (`POST /documents`), consumed by the worker in M1.

## Context

Document ingestion runs asynchronously: an upload returns immediately and a worker
parses, chunks, and embeds the file afterwards. That requires a queue.

The conventional choice is Redis with RQ or Celery. It brings push delivery, built-in
retries and scheduling, and high throughput. It also brings a service to run in every
environment — a Compose service, a Kubernetes Deployment, and either ElastiCache or a
self-managed instance on AWS.

The decisive problem is not operational cost but atomicity. Enqueueing to a separate
broker is a second write that cannot participate in the database transaction:

```python
session.add(document)
session.add(job)
session.commit()  # PostgreSQL
redis.lpush("queue", ...)  # a different system
```

A crash between those two statements leaves a document with no queued work and nothing
to notice. The usual remedies — a transactional outbox with a relay, or a reconciler
sweeping for orphans — amount to using PostgreSQL to make Redis reliable.

Job state also has to be durable and queryable regardless: `GET /jobs/{job_id}` needs
status, attempt count, error text, and timestamps. Redis holds transient messages, not
history, so a Redis-based design keeps these rows in PostgreSQL anyway and maintains
two stores where one would do.

## Decision

The `ingestion_jobs` table is the queue. Workers claim rows with
`SELECT ... FOR UPDATE SKIP LOCKED`, in a single statement:

```sql
UPDATE ingestion_jobs SET status = 'running', attempts = attempts + 1, heartbeat_at = now()
WHERE id = (SELECT id FROM ingestion_jobs
            WHERE status = 'queued'
               OR (status = 'running' AND heartbeat_at < now() - interval '5 min')
            ORDER BY created_at LIMIT 1
            FOR UPDATE SKIP LOCKED)
RETURNING *;
```

`SKIP LOCKED` lets concurrent workers pass over rows their peers have locked rather
than blocking on them, so claiming is contention-free without an external broker.

Enqueueing is part of the same transaction as the document it belongs to:

```python
session.add(document)
session.add(job)
session.commit()  # both rows, or neither
```

## Consequences

**Gained**

- Enqueue is atomic with the data it describes. No outbox, no reconciler, no orphaned
  documents.
- One fewer service in Compose, in Kubernetes, and on AWS.
- Job history is already where the API needs it, in a table that joins to `documents`.
- Crash recovery is a `heartbeat_at` predicate rather than a broker feature: a worker
  that dies mid-job has its work reclaimed after five minutes.

**Given up**

- Delivery is polled, not pushed. A worker picks up work within roughly one second
  rather than milliseconds. Ingestion takes tens of seconds, so this is not observable.
- Throughput is bounded by row-level locking — thousands per second rather than
  hundreds of thousands. The expected load is a few jobs per minute.
- Retries, backoff, and scheduling are hand-written. Bounded `attempts` plus heartbeat
  reclaim is about fifteen lines; it would not stay cheap across many job types.
- Redis is unavailable for the things it is genuinely good at here. Response caching
  and rate limiting are parked, and either would be a legitimate reason to add it later
  without revisiting this decision.

**Revisit if** sustained throughput approaches the locking ceiling, scheduled or
chained tasks are needed, or workers in another language must consume the same queue.

## Evidence

`POST /documents` performs the single-transaction insert, and
`tests/integration/test_documents.py::test_upload_creates_document_and_job_together`
asserts both rows exist via a join.

`tests/integration/test_jobs.py::test_concurrent_workers_never_claim_the_same_job`
releases eight workers together against forty queued jobs and asserts each job is
claimed exactly once, with `attempts` still 1. That guarantee comes from the claim being
a single atomic statement, and the test passes even with `SKIP LOCKED` removed.
`test_a_claim_skips_a_row_another_worker_holds` is what fails then: with the oldest job
locked, a claim must take the next one rather than wait behind the lock, so it is the
evidence that claiming does not block.

`tests/integration/test_pipeline.py::test_a_job_whose_worker_died_is_reclaimed_and_indexed_once`
kills a worker partway through a job, with the staleness window shortened to seconds. The
job is left alone while the dead worker's heartbeat is fresh, then reclaimed by the next
poll once it goes stale, and the document is indexed exactly once.
`test_a_job_that_kills_every_worker_is_failed_once_its_attempts_run_out` shows the bound:
after three deaths the job is failed rather than reclaimed again.
