# The evaluation harness, measured against the local database. It needs PostgreSQL with
# migrations applied; see docs/success-criteria.md for what each figure means.
.PHONY: eval eval-live

# Retrieval figures, with the answering path run by the stub: free, and not measured.
eval:
	uv run python -m eval

# Answers with Claude on ANTHROPIC_API_KEY, which costs about $1–2 a run.
eval-live:
	uv run python -m eval --answers claude
