# Development guidelines

## General
- Prefer simple implementations over unnecessary abstractions.
- Do not add dependencies without explaining why.
- Do not modify unrelated files.
- Keep functions reasonably small.
- Use type hints.

## Workflow
- Before major changes, explain the proposed implementation.
- Work on one roadmap item at a time.
- Do not commit changes unless explicitly instructed.
- Commit messages must be approved before commits are finalized.
- Do not implement future roadmap items early.

## Testing
- Add tests for meaningful functionality.
- Run the relevant tests after changes.
- Report test results.
- Never mock the database. The vector query is the system; a mocked session proves
  nothing. Integration tests run against real PostgreSQL and MinIO.
- `tests/unit/` does no I/O and always runs. `tests/integration/` needs the Compose
  stack and skips with a helpful message when it is unavailable, so the suite stays
  green without Docker and runs fully in CI.
- Verify with all four checks, not a subset: `ruff check .`, `ruff format --check .`,
  `mypy .`, `pytest`. Ruff formats Python inside Markdown code blocks too.

## Project docs
- `docs/roadmap.md` — backlog and current position. Update as items complete.
- `docs/architecture.md` — system architecture. Update as subsystems land.
- Subsystem docs live alongside in `docs/` and are written when the subsystem exists,
  not before.
