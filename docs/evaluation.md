# Evaluation

The evaluation harness scores the service against a frozen corpus of six NASA technical
reports and a golden set of 30 answerable and 8 unanswerable questions. What each figure
means, and the targets it is judged against, are fixed in
[success-criteria.md](success-criteria.md#scoring); the plan, the corpus selection, and
the rules decided before any result existed are in
[roadmap.md](roadmap.md#m3--eval-harness).

## Running it

Both commands need PostgreSQL with migrations applied
(`docker compose up -d postgres` and `uv run alembic upgrade head`). Neither needs MinIO
or the worker.

| Command | Answers with | Cost | Measures |
| --- | --- | --- | --- |
| `make eval` | the stub generator | free | retrieval and the prefix comparison; runs the answering path without measuring it |
| `make eval-live` | `claude-opus-5`, on `ANTHROPIC_API_KEY` | about $1–2 a run | everything above, plus refusal, citation validity, and false refusals |

`make eval-live` reads `ANTHROPIC_API_KEY` from `.env` or from the environment of the shell
that runs it, whatever `GENERATOR` says; `make eval` needs no key.

CI runs `make eval`'s equivalent after the tests on every pull request and push to
`main`, and writes the summary to the run's page. A missed target is recorded there, never
failed: only a harness that cannot run fails the step.

Each run prints a Markdown summary and writes `eval/results/<UTC start time>.json`, which
git ignores. A run cited in the documentation is committed with `git add -f`.

To record a run worth citing, start it from a clean commit and leave tracked files alone
until it finishes. The run reads git's state when it writes its record, at the end, so an
edit made mid-run marks it as having uncommitted changes. Untracked files do not count.

Run one evaluation at a time against a database: each run replaces the eval collection
the previous one wrote.

## How a run works

1. **Verify the corpus.** Every PDF in `eval/corpus/` is hashed against
   `manifest.sha256`, a `shasum -a 256` listing of each file's SHA-256 digest. A missing,
   changed, or unlisted file stops the run: results over different text are not
   comparable.
2. **Load the golden set.** `eval/golden.toml` is checked for structure: exact fields,
   evidence naming corpus documents, whole-number pages, unique ids, five answerable
   questions per report, and eight unanswerable ones.
3. **Ingest.** The corpus is parsed, chunked, and embedded with the service's own steps,
   then written in one transaction into a `golden-corpus` collection owned by
   `eval@groundwork.invalid`, replacing that user's previous collections. Object storage
   and the job queue are skipped; neither changes what retrieval can find. The eval user
   is not the default user, so test fixtures never delete eval data.
4. **Check the evidence.** Every quote must appear on the page it names in the parsed
   text, compared case-insensitively with whitespace collapsed, and be under 48 tokens.
   Consecutive chunks overlap by at least 64, so each quote lies whole within some chunk.
5. **Retrieval.** Each answerable question is searched twice with the service's
   `dense_search`: five results for Recall@5, the search the service answers from, and ten
   for MRR@10. A chunk is a hit when it comes from the evidence's document, its pages
   include the evidence page, and its text contains the quote. Every question's best chunk
   score is recorded.
6. **Prefix comparison.** The same questions are retrieved without and with bge's query
   instruction, and the pre-registered rule is applied.
7. **Answering.** Every question goes through `answer_question`, as `POST /questions` runs
   it, and is scored from what the service recorded in `questions` and
   `retrieval_results`.
8. **Record.** The JSON carries the figures, each question's detail, and the git commit
   (with whether tracked files had changed), corpus and golden-set digests, embedding
   model, chunk settings, pgvector version, and generation model.

## Golden set provenance

The questions in `eval/golden.toml` were written by `gpt-5.6-sol` on 2026-09-12 from the
source PDFs only. The authoring model was not shown parsed chunks, retrieval results,
generated answers, or evaluation measurements.

The set contains 30 answerable questions, distributed evenly across the six reports, and
eight unanswerable questions. Evidence pages count positions in each PDF from the cover as
page 1; they are not printed page numbers. Evidence quotes are short, verbatim spans of
body prose from a single page. Each unanswerable question was checked against the complete
text of all six reports.

Question wording is never changed to fix a result or a typo; CLAUDE.md records the rule,
and an approved change needs a dated note in success-criteria.md.

## Results

Recorded 2026-09-12 by `make eval-live` at commit `2047afb`, with no uncommitted changes
to tracked files: [`eval/results/20260912T165717Z.json`](../eval/results/20260912T165717Z.json).
pgvector 0.8.6, bge-small-en-v1.5, 510-token chunks overlapping by 64 (262 chunks), and
answers from `claude-opus-5`. This is one run. Retrieval is deterministic, and CI's Linux
runner reproduced its figures exactly, but a model's answers can vary between runs.

### Against the criteria

| Criterion | Target | Result |
| --- | --- | --- |
| Recall@5 | ≥ 0.90 (n=30) | 27/30 = 0.900: met, exactly on the target |
| Citations | validation rejects 100% of invalid citations | 0 of 49 returned markers named an uncited chunk: met |
| Refusal | 8/8 unanswerable questions refused | 8/8: met |

At n=30 one question moves Recall@5 by 0.033, so one more miss would have missed the
target, and the 95% Wilson interval around 0.90 is roughly [0.74, 0.97].

### Reported alongside

| Figure | Result |
| --- | --- |
| MRR@10 (n=30) | 0.763 |
| False refusals | 3/30: `a23`, `a28`, `a30` |
| Declined / failed (n=38) | 0 / 0 |
| Raw invalid-citation rate | 0/49, over 38 questions checked |
| Tokens | 181,862 input and 7,176 output, about $1.09 at list prices |

**Misses.** Retrieval missed `a23` (its evidence ranked 7th), `a28` (10th), and `a30`
(not in the top 10). They are exactly the three false refusals: given passages without
the answer, the model reported insufficient evidence rather than answering from anything
else. Every false refusal in this run is a retrieval miss, so better retrieval is what
would reduce them.

**Citations.** The model cited only passages it was given, so validation rejected
nothing and this run could not show a rejection. That invalid numbers are rejected and
never reach a returned answer is shown instead by
`test_invalid_citations_are_never_returned_or_recorded` and
`test_answers_are_scored_from_what_the_service_recorded`, which supply citations of
passages that were never given.

**Latency**, milliseconds per stage, P50 / P95 (n=38): embed 15 / 19, search 6 / 8, prep
0 / 0, model 3,132 / 6,568, and everything but the model 21 / 27. The questions ran one at
a time on an idle laptop, which says little about P95 under the traffic the latency
criterion is meant for, so these are not recorded against it.

### Query instruction prefix

The pre-registered comparison, over the same 30 answerable questions:

| Figure | Without | With `Represent this sentence for searching relevant passages: ` |
| --- | --- | --- |
| Recall@5 | 27/30 = 0.900 | 26/30 = 0.867 |
| MRR@10 | 0.763 | 0.786 |

The prefix fixed no question and broke `a22`, a net −1 against the +2 the rule required.
**Questions stay unprefixed.** MRR@10 rose, but the rule requires both conditions.

### Relevance threshold

Best chunk scores do not separate the two kinds of question. Answerable questions range
0.663–0.873 (median 0.764, n=30); unanswerable ones 0.724–0.871 (median 0.779, n=8). A
cutoff above every unanswerable score would refuse 29 of the 30 answerable questions, and
one at the lowest unanswerable score would refuse 7 answerable questions and none of the
unanswerable ones. **No threshold is applied**, as the pre-registered rule required; the
model's own judgement refused all 8.

## Limits

- **This corpus, these questions.** With n=30 and n=8, the figures describe retrieval
  and answering over these six reports, not in general.
- **One question author.** The golden set was written by one model, `gpt-5.6-sol`, whose
  phrasing could favour one retriever over another. It is not the model that answers.
- **One live run.** Refusal and citation figures come from a single run of a model whose
  answers can vary.
- **Groundedness is not scored.** Every returned citation must name a passage the model
  was given, but whether the passage supports the statement is not judged; see
  [success-criteria.md](success-criteria.md#deliberately-not-measured).
- **Text-native PDFs without ligatures.** Parsing keeps typographic ligatures, which
  break exact matching, and the corpus was chosen to contain none; the fix is parked in
  the roadmap.
