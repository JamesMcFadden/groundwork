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

## Project docs
- `docs/roadmap.md` — backlog and current position. Update as items complete.
- `docs/architecture.md` — system architecture. Update as subsystems land.
- Subsystem docs live alongside in `docs/` and are written when the subsystem exists,
  not before.
