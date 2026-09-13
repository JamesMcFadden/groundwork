# The evaluation harness, measured against the local database. It needs PostgreSQL with
# migrations applied; see docs/success-criteria.md for what each figure means.
.PHONY: eval eval-live kind-images

# Retrieval figures, with the answering path run by the stub: free, and not measured.
eval:
	uv run python -m eval

# Answers with Claude on ANTHROPIC_API_KEY, which costs about $1–2 a run.
eval-live:
	uv run python -m eval --answers claude

# The application image for the kind cluster, built from the working tree and loaded into
# its node, which never pulls it. The tag stays `kind`, so the manifests never change; the
# commit it was built from, marked `-dirty` for uncommitted changes, is the image's
# revision label, which load results record.
kind-images:
	docker build --target api \
		--label org.opencontainers.image.revision=$$(git describe --always --dirty --abbrev=40) \
		-t groundwork-api:kind .
	kind load docker-image --name groundwork groundwork-api:kind
