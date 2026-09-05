# Roadmap

Lightweight backlog. Detail is added for the current milestone only; future milestones
stay one line until they are next.

**Now:** M0 — Foundations + document API
**Branching:** M0 lands on `main`; from M1 each milestone gets a branch and a
CI-gated PR.
**Next item:** `build: add multi-stage Dockerfile with api and worker targets`

## Milestones

- [ ] **M0** Foundations + document API — tooling, compose, Dockerfile, schema, upload
- [ ] **M1** Async ingestion — worker, skip-locked queue, parse/chunk/embed
- [ ] **M2** RAG query path — vector search, Claude call, cited answers
- [ ] **M3** Eval harness — golden set (30 answerable + 8 unanswerable), Recall@5,
      citation validity, refusal
- [ ] **M4** Hybrid retrieval — FTS + RRF, dense-vs-hybrid ablation
- [ ] **M5** Hardening — API key, query scoping, logging, 503, reindex
- [ ] **M6** Kubernetes on kind — manifests, probes, scaling and pod-kill evidence
- [ ] **M7** AWS — Terraform (S3/ECR/RDS), eksctl + IRSA, deploy and tear down
- [ ] **M8** CI/CD + write-up — ECR push, README results, runbook

## M0 — Foundations + document API

- [x] `chore: init uv project with ruff, mypy, pytest`
- [x] `chore: pin vscode interpreter to project venv`
- [x] `docs: add success criteria and target metrics`
- [x] `build: add docker compose with postgres/pgvector and minio`
- [x] `feat(api): add app factory with live and ready health endpoints`
- [ ] `build: add multi-stage Dockerfile with api and worker targets`
- [ ] `feat(db): add schema for collections, documents, chunks, and jobs`
- [ ] `feat(db): add initial alembic migration`
- [ ] `feat(api): add collections create and list with keyset pagination`
- [ ] `feat(storage): add s3 client with minio-compatible config`
- [ ] `feat(api): add POST /documents with atomic document and job insert`
- [ ] `ci: run lint, type check, and tests on pull requests`
- [ ] `docs(adr): use postgres skip-locked queue instead of redis`
- [ ] `docs(adr): use eksctl for cluster, terraform for data services`

## Carried decisions

Decisions taken ahead of their milestone, recorded so they are not lost. Do not
implement early; apply when the milestone is reached. Rationale in
[success-criteria.md](success-criteria.md).

- **M2** — add a `GENERATOR=stub` flag selecting the fake answer generator, so load
  tests measure this service rather than the LLM provider.
- **M3** — cache fastembed model weights in CI; the ONNX download is ~100 MB per run.
- **M6** — scale workers to 0 and set explicit CPU requests/limits during load runs, or
  the 1→3 replica comparison measures contention rather than scaling.

## Parked

Ideas deliberately out of scope for now. Do not start these; they exist so good ideas
have somewhere to go that is not the current branch.

- HPA + metrics-server
- Hashed API-key table for real multi-tenancy
- LLM judge for answer groundedness
- k6 load matrix run against EKS (currently kind only)
- Redis response cache
