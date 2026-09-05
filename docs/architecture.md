# Architecture

Updated as subsystems land. Sections describe what exists, not what is planned —
the plan lives in [roadmap.md](roadmap.md).

## Shape

A RAG knowledge service with two runtime components sharing one database:

- **API** — accepts uploads and questions, serves answers with citations
- **Worker** — ingests uploaded documents asynchronously

State lives in PostgreSQL; original documents live in object storage. Neither
component exists yet.

## Current state

Repository skeleton only.

- Python 3.12, dependencies managed by `uv`, the `app` package installed into the
  project venv so imports resolve identically in tests, CI, and containers
- Lint and format by Ruff, type checking by mypy, tests by pytest
- No application code

## Decisions

### Job queue in PostgreSQL rather than Redis

The `ingestion_jobs` table is the queue, claimed with `FOR UPDATE SKIP LOCKED`. A
document and its job commit in one transaction, so there is no dual-write problem and
no separate broker to run.

See [ADR 0001](adr/0001-postgres-skip-locked-queue.md).

### eksctl for the cluster, Terraform for data services

Terraform manages S3, ECR, and RDS. The EKS cluster and its IRSA service account are
created with `eksctl`, which collapses hours of hand-written OIDC configuration into
two commands. Reproducibility for the stateful resources is what matters; the cluster
is created and destroyed per demo.

_To be recorded as an ADR when infrastructure is implemented (M7)._

## Sections to be written

Added as each subsystem is built: data model, ingestion pipeline, retrieval, answer
generation and citations, evaluation, deployment.
