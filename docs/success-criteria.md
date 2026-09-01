# Success criteria

These targets were fixed before anything was measured. That is the point of the file:
choosing criteria after seeing results means choosing flattering ones.

Two rules govern it:

- **Record, do not block.** A missed target is written down with an explanation, not
  quietly retuned. A documented miss is a better artifact than a moved goalpost.
- **Changes need a dated note.** If a criterion is revised, the reason goes in
  [Revisions](#revisions) with the date. Silent edits defeat the file.

## Criteria

| Area | Criterion | Measured by | Status |
| --- | --- | --- | --- |
| Retrieval | Recall@5 ≥ 0.90 (n=30) | `make eval` | not yet measured |
| Citations | Validation rejects 100% of invalid chunk ids | `make eval` | not yet measured |
| Refusal | 8/8 unanswerable questions refused | `make eval` | not yet measured |
| API reliability | ≥ 99% non-5xx, 30 VU × 5 min, stubbed generator | k6 on kind | not yet measured |
| Latency | P95 `POST /questions` < 500 ms excluding LLM | timings in `questions` | not yet measured |
| Ingestion | 30-page PDF indexed in < 60 s | job timestamps | not yet measured |
| Scaling | 1→3 API replicas ≥ 1.8× throughput, P95 no worse | k6 on kind | not yet measured |
| Recovery | Pod deletion under load → zero failed requests | k6 error rate | not yet measured |
| AWS | Reachable via LoadBalancer, answering against RDS | smoke test | not yet measured |
| Deployment | Fresh environment from documented steps; one `docker compose up` | manual, timed | not yet measured |
| CI | Every PR runs lint, types, tests, retrieval eval | GitHub Actions | not yet measured |

Reported alongside, with no target attached:

- **MRR@10.** A useful diagnostic, but gating on it is stricter than the recall target
  and would muddy the headline result.
- **Raw invalid-citation rate**, measured before validation rejects anything. The
  criterion above is a hard gate the code enforces, so it passes by construction; this
  number is the interesting one, and it is what justifies having written the validator.
- **Per-stage latency breakdown** — embedding, vector search, prompt assembly, LLM.

## Sample size

The golden set is **30 answerable + 8 unanswerable questions** over 6 documents.

At n=30 one question moves Recall@5 by 3.3%, and 27/30 lands exactly on the 0.90
target. The 95% Wilson interval at 0.90 is still wide — roughly [0.74, 0.97] — so
**every reported figure carries its n**, and no claim is made that this measures
retrieval quality in general. It measures retrieval quality on this corpus.

Comparisons between retrieval strategies are run on the identical question set, so
they are paired and considerably more sensitive than those marginal intervals imply.
Report them as flip counts — how many questions a change fixed and how many it broke —
not as two overlapping confidence intervals.

## Deliberately not measured

**Answer groundedness.** Scoring whether an answer is supported by its retrieved
context needs an LLM judge, and an uncalibrated judge produces a number without a
meaning. Validating one against human labels was cut for time. Tracked in the
roadmap's Parked list; the honest statement is that grounding is enforced structurally
(citations must resolve to supplied chunks) but not scored semantically.

## Scope boundaries affecting these numbers

- **Text-native PDFs only.** No OCR, no scanned documents. Retrieval quality is bounded
  by extraction quality, so this constraint sits next to the retrieval target rather
  than buried elsewhere.
- **Load tests run with a stubbed answer generator.** Driving thousands of live LLM
  calls would measure the provider's throughput and rate limits, not this service.
  Everything Kubernetes replicas actually affect — routing, pooling, vector search,
  serialization — is still exercised.
- **Scaling and recovery are measured on kind**, not EKS. AWS proves the deployment
  path; the cluster experiments run locally where they are free and repeatable.

## Revisions

_None yet._
