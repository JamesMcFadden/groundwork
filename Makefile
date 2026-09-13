# The evaluation harness, measured against the local database. It needs PostgreSQL with
# migrations applied; see docs/success-criteria.md for what each figure means.
.PHONY: eval eval-live kind-images ecr-images

# Retrieval figures, with the answering path run by the stub: free, and not measured.
eval:
	uv run python -m eval

# Answers with Claude on ANTHROPIC_API_KEY, which costs about $1–2 a run.
eval-live:
	uv run python -m eval --answers claude

# The application images for the kind cluster, built from the working tree and loaded into
# its node, which never pulls them. Tags stay `kind`, so the manifests never change; the
# commit they were built from, marked `-dirty` for uncommitted changes, is each image's
# revision label, which load results record.
kind-images:
	revision=$$(git describe --always --dirty --abbrev=40) && \
	docker build --target api --label org.opencontainers.image.revision=$$revision -t groundwork-api:kind . && \
	docker build --target worker --label org.opencontainers.image.revision=$$revision -t groundwork-worker:kind .
	kind load docker-image --name groundwork groundwork-api:kind groundwork-worker:kind

# The application images for EKS, pushed to the ECR repositories infra/data creates, for
# arm64 like the nodes. Tagged with the commit, which ECR refuses to overwrite, so they are
# built only when every file that goes into an image matches that commit: otherwise a tag
# would name code it does not hold, and no later build could correct it. A tag already
# pushed is left alone. Deployed by the digest ECR records. No provenance attestation:
# Docker Desktop's builder would wrap each image in an index beside one, which nothing here
# reads, so a tag would name the index rather than the image ECR scans.
IMAGE_INPUTS := app migrations Dockerfile .dockerignore pyproject.toml uv.lock README.md alembic.ini

ecr-images:
	@test -z "$$(git status --porcelain -- $(IMAGE_INPUTS))" || \
		{ echo "refusing to push: image inputs differ from HEAD"; git status --short -- $(IMAGE_INPUTS); exit 1; }
	revision=$$(git rev-parse HEAD) && \
	registry=$$(terraform -chdir=infra/data output -json repository_urls | \
		python3 -c 'import json, sys; print(json.load(sys.stdin)["groundwork-api"].split("/")[0])') && \
	aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $$registry && \
	for target in api worker; do \
		if aws ecr describe-images --region us-east-1 --repository-name groundwork-$$target \
			--image-ids imageTag=$$revision >/dev/null 2>&1; then \
			echo "groundwork-$$target:$$revision is already pushed"; continue; \
		fi; \
		docker build --platform linux/arm64 --provenance=false --target $$target \
			--label org.opencontainers.image.revision=$$revision \
			-t $$registry/groundwork-$$target:$$revision . && \
		docker push $$registry/groundwork-$$target:$$revision || exit 1; \
	done
