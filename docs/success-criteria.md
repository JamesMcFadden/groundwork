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
| Retrieval | Recall@5 ≥ 0.90 (n=30) | `make eval` | met: 29/30 = 0.967 (hybrid, 2026-09-12, [runs](evaluation.md#results)); dense search, which hybrid replaced, was exactly on target at 27/30 |
| Citations | Validation rejects 100% of invalid citations | `make eval-live` | met: 0 of 54 returned markers named an uncited chunk (n=38, hybrid, `claude-opus-5`, 2026-09-12); the model cited nothing invalid, so rejection itself is shown by tests |
| Refusal | 8/8 unanswerable questions refused | `make eval-live` | met: 8/8, with 1/30 false refusals (hybrid, `claude-opus-5`, 2026-09-12); dense search had 3/30 |
| API reliability | ≥ 99% non-5xx, 30 VU × 5 min, stubbed generator | k6 on kind | met: 100% in each of 6 runs, 140,828 requests with no 5xx and none unanswered (2026-09-13, [runs](roadmap.md#m6--kubernetes-on-kind)) |
| Latency | P95 `POST /questions` < 500 ms excluding LLM | timings in `questions` | met: 243 ms over 94,095 questions in the 3-replica load runs on kind; 249 ms at 1 replica (2026-09-13) |
| Ingestion | 30-page PDF indexed in < 60 s | job timestamps | met locally: 4.05 s (n=1, laptop CPU via Compose, 2026-09-11) |
| Scaling | 1→3 API replicas ≥ 1.8× throughput, P95 no worse | k6 on kind | met: 2.12× median throughput, 111.4 against 52.6 requests/s, and median P95 381 against 941 ms (n=3 runs each, 2026-09-13); the third pair alone gave 1.64× |
| Recovery | Graceful pod deletion under load → zero failed requests | k6 error rate | met: 0 failed requests in each of 3 runs, one of 3 API pods deleted 2 minutes in (2026-09-13) |
| AWS | Reachable via LoadBalancer, answering against RDS | smoke test | met: every check passed at `https://api.groundworkproj.com`, served over TLS through a Network Load Balancer on EKS; `uam-risk.pdf` indexed in 23.1 s and the golden question `a21` answered citing it, against RDS PostgreSQL 16.15 with pgvector 0.8.2 (2026-09-14, [run](roadmap.md#m7--aws)) |
| Deployment | Fresh environment from documented steps; one `docker compose up` | `load/deployment.py`, timed | met: a clone with no `.env`, no images, and an empty build cache came up and answered on `cp .env.example .env` and one `docker compose up`, in 39.3 s to a ready API (2026-09-18, [run](roadmap.md#m8--cicd--write-up)) |
| CI | Every PR runs lint, types, tests, retrieval eval | GitHub Actions | met: all four run on every pull request and push to `main` (from PR #4, 2026-09-12) |

Reported alongside, with no target attached:

- **MRR@10.** A useful diagnostic, but gating on it is stricter than the recall target
  and would muddy the headline result.
- **Raw invalid-citation rate**, measured before validation rejects anything. The
  criterion above is a hard gate the code enforces, so it passes by construction; this
  number is the interesting one, and it is what justifies having written the validator.
- **Per-stage latency breakdown** — embedding, vector search, prompt assembly, LLM.
- **False refusals.** Answerable questions recorded as insufficient evidence. Without
  this figure, a service that refused every question would still meet the refusal
  criterion.

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

Two rules govern how the set is built, because both are easy to get wrong and either
invalidates every number above:

- **Write questions from the documents, not from the chunks.** Reading chunks while
  writing questions grades the chunker against itself and inflates recall.
- **Freeze the corpus.** Adding or re-parsing documents makes runs incomparable, so the
  ablation between retrieval strategies stops meaning anything.

## Scoring

Fixed in M3 alongside the golden set's format, before any run.

- **Hit.** Each answerable question lists one or more places its answer is stated: a
  document, a page, and a short verbatim quote from that page. A retrieved chunk is a
  hit when it comes from that document, its pages include that page, and its text
  contains the quote, compared case-insensitively with whitespace collapsed. Page
  overlap alone would credit neighbouring chunks that do not hold the answer. Quotes are
  kept under 48 tokens, and consecutive chunks overlap by at least 64, so every quote
  lies whole within some chunk.
- **Recall@5** is the share of the 30 answerable questions with a hit among the five
  chunks the service's own search returns. With one fact per question it is strictly a
  hit rate; the conventional name is kept.
- **MRR@10** averages, over answerable questions, the reciprocal rank of the first hit
  in a separate ten-chunk search, counting 0 where there is none. The searches are kept
  separate so an approximate index can never make Recall@5 differ from what the service
  retrieves.
- **Refusal** counts unanswerable questions recorded as `insufficient_evidence`. A
  declined or failed question is not a refusal, and is reported separately.
- **Citation validity** reads every `[n]` marker back out of a returned answer and
  requires it to name a chunk retrieved for that question and marked as cited. The raw
  invalid-citation rate is rejected citation numbers over rejected plus accepted, each
  counted once per question, across questions whose answers reached validation.

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

- **2026-09-12, before any measurement.** The citations criterion names invalid
  citations rather than invalid chunk ids: since M2, chunk ids never reach the model,
  which cites passage numbers. [Scoring](#scoring) defines hits and each golden-set
  figure, and false refusals join the figures reported alongside. No target changed.
- **2026-09-12, recording the first results.** Citations and refusal are measured by
  `make eval-live`, the run that answers with Claude; `make eval` answers with a stub that
  cannot refuse or miscite, so it leaves both unmeasured. Only the command named in
  "Measured by" changed. No target changed.
- **2026-09-18, recording the result.** Deployment is measured by `load/deployment.py`
  rather than by hand: the run has preconditions a person cannot be trusted to hold in
  their head, and the script refuses to run with the images built, the volumes present, or
  any build cache. Only the entry in "Measured by" changed. No target changed, and the
  criterion had none to change: the time is reported alongside, as the rule fixed in M8
  says.
- **2026-09-13, before any measurement.** Recovery names graceful pod deletion: a pod
  deleted with its grace period, as `kubectl delete pod` does by default. A forced
  deletion or a crash loses the requests in flight on that pod whatever the service does,
  so zero failures could never be met for one. The wording was reviewed while planning
  M6, when uvicorn's source showed that its shutdown can fail a request sent on an idle
  connection; the target stayed at zero, and the plan drains connections before shutdown
  instead. No target changed.
