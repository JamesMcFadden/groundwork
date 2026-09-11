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

## Merging pull requests
- Merge only when explicitly instructed.
- Update the PR description first so it matches what is being merged.
- Mark a draft PR ready for review before merging; GitHub will not merge a draft.
- Merge only after CI passes on the PR's current head commit: wait for the run for that
  exact SHA to finish successfully, and confirm `gh pr checks` reports every check
  passing. If either fails, do not merge; report why. Branch protection is unavailable
  on this private repo's plan, so this check is the only gate.
- Pin the merge to the verified commit, so it is refused if the head has moved since:
  `gh pr merge <number> --merge --delete-branch --match-head-commit <sha>`.
- Use a merge commit, never squash or rebase. Branch commits keep their SHAs, which PR
  descriptions and docs cite; squashing collapses them and rebasing rewrites them.
- After merging, confirm CI passes on `main` for the merge commit.

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
