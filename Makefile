# The evaluation harness, measured against the local database. It needs PostgreSQL with
# migrations applied; see docs/success-criteria.md for what each figure means.
.PHONY: eval
eval:
	uv run python -m eval
