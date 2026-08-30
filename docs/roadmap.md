# Roadmap

Lightweight backlog. Detail is added for the current milestone only; future milestones
stay one line until they are next.

**Now:** M0 — Foundations + document API
**Next item:** `docs: add success criteria and target metrics`

## Milestones

- [ ] **M0** Foundations + document API — tooling, compose, Dockerfile, schema, upload
- [ ] **M1** Async ingestion — worker, skip-locked queue, parse/chunk/embed
- [ ] **M2** RAG query path — vector search, Claude call, cited answers
- [ ] **M3** Eval harness — golden set, Recall@k / MRR / citation validity / refusal
- [ ] **M4** Hybrid retrieval — FTS + RRF, dense-vs-hybrid ablation
- [ ] **M5** Hardening — API key, query scoping, logging, 503, reindex
- [ ] **M6** Kubernetes on kind — manifests, probes, scaling and pod-kill evidence
- [ ] **M7** AWS — Terraform (S3/ECR/RDS), eksctl + IRSA, deploy and tear down
- [ ] **M8** CI/CD + write-up — ECR push, README results, runbook

## M0 — Foundations + document API

- [x] `chore: init uv project with ruff, mypy, pytest`
- [x] `chore: pin vscode interpreter to project venv`
- [ ] `docs: add success criteria and target metrics`
- [ ] `build: add docker compose with postgres/pgvector and minio`
- [ ] `feat(api): add app factory with live and ready health endpoints`
- [ ] `build: add multi-stage Dockerfile with api and worker targets`
- [ ] `feat(db): add schema for collections, documents, chunks, and jobs`
- [ ] `feat(db): add initial alembic migration`
- [ ] `feat(api): add collections create and list with keyset pagination`
- [ ] `feat(storage): add s3 client with minio-compatible config`
- [ ] `feat(api): add POST /documents with atomic document and job insert`
- [ ] `ci: run lint, type check, and tests on pull requests`
- [ ] `docs(adr): use postgres skip-locked queue instead of redis`
- [ ] `docs(adr): use eksctl for cluster, terraform for data services`

## Parked

Ideas deliberately out of scope for now. Do not start these; they exist so good ideas
have somewhere to go that is not the current branch.

- HPA + metrics-server
- Hashed API-key table for real multi-tenancy
- LLM judge for answer groundedness
- k6 load matrix run against EKS (currently kind only)
- Redis response cache
