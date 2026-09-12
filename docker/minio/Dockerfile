# syntax=docker/dockerfile:1

# MinIO for local development and CI, built from source.
#
# MinIO stopped publishing community images and binaries, and the Docker Hub images this
# project pinned were deleted in September 2026. This builds the last community release,
# unmodified, from its tagged source. The project is archived, so nothing here will be
# patched, including against its 2026 security advisories: it stands in for S3 on
# localhost and CI runners only. Production uses S3.
#
# Published by .github/workflows/minio-image.yml, which reads MINIO_TAG from this file,
# to ghcr.io/jamesmcfadden/groundwork-minio. That package is public, and GitHub never
# lets a public package go private again.
#
# To move to another MinIO release:
#   1. Set MINIO_TAG and MINIO_COMMIT below, and push. The workflow publishes the new
#      tag; a tag that is already published is never rebuilt.
#   2. Copy the pinned reference from that workflow run's summary.
#   3. Pin it in both docker-compose.yml and .github/workflows/ci.yml. Nothing checks
#      that the two agree.

ARG MINIO_TAG=RELEASE.2025-10-15T17-29-55Z
ARG MINIO_COMMIT=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a

# ---- build ---------------------------------------------------------------
# Runs on the builder's own platform and cross-compiles. MinIO is pure Go, so an amd64
# runner produces the arm64 binary directly rather than under emulation.
FROM --platform=$BUILDPLATFORM golang:1.24-alpine@sha256:8bee1901f1e530bfb4a7850aa7a479d17ae3a18beb6e09064ed54cfd245b7191 AS build
ARG MINIO_TAG
ARG MINIO_COMMIT
ARG TARGETOS
ARG TARGETARCH

RUN apk add --no-cache git

WORKDIR /src
# A tag can be moved; a commit cannot. Refuse to build anything but the reviewed source.
RUN git clone --depth 1 --branch "$MINIO_TAG" https://github.com/minio/minio.git . \
 && test "$(git rev-parse HEAD)" = "$MINIO_COMMIT"

RUN go mod download

# Flags are generated on the build platform, then the binary is compiled for the target.
# MINIO_RELEASE and the release date stamp it as the release it is, as MinIO's own
# release builds did, rather than as a development build.
RUN release_date=$(echo "${MINIO_TAG#RELEASE.}" | sed -E 's/T([0-9]{2})-([0-9]{2})-([0-9]{2})Z$/T\1:\2:\3Z/') \
 && ldflags=$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go "$release_date") \
 && CGO_ENABLED=0 GOOS="$TARGETOS" GOARCH="$TARGETARCH" \
    go build -tags kqueue -trimpath -ldflags "$ldflags" -o /out/minio

# ---- runtime -------------------------------------------------------------
# Alpine rather than scratch, so Compose can health-check with busybox wget.
FROM alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
ARG MINIO_TAG
ARG MINIO_COMMIT
LABEL org.opencontainers.image.title="groundwork-minio" \
      org.opencontainers.image.description="MinIO $MINIO_TAG, built unmodified from https://github.com/minio/minio at $MINIO_COMMIT" \
      org.opencontainers.image.version="$MINIO_TAG" \
      org.opencontainers.image.licenses="AGPL-3.0" \
      org.opencontainers.image.source="https://github.com/JamesMcFadden/groundwork"

COPY --from=build /out/minio /usr/bin/minio

EXPOSE 9000
ENTRYPOINT ["minio"]
CMD ["server", "/data"]
