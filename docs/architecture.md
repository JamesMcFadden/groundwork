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

Ingestion jobs are claimed with `SELECT ... FOR UPDATE SKIP LOCKED` against an
`ingestion_jobs` table. The document row and its job row commit in one transaction,
which removes the dual-write problem a separate broker would introduce — there is no
outbox and no reconciler. It also removes a container from Compose, a Deployment from
Kubernetes, and a service from AWS.

Cost: no push delivery, so the worker polls. Acceptable at this scale.

_To be recorded as an ADR when the queue is implemented (M1)._

### eksctl for the cluster, Terraform for data services

Terraform manages S3, ECR, and RDS. The EKS cluster and its IRSA service account are
created with `eksctl`, which collapses hours of hand-written OIDC configuration into
two commands. Reproducibility for the stateful resources is what matters; the cluster
is created and destroyed per demo.

_To be recorded as an ADR when infrastructure is implemented (M7)._

## Sections to be written

Added as each subsystem is built: data model, ingestion pipeline, retrieval, answer
generation and citations, evaluation, deployment.
