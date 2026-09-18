# Runbook

What to do when something is wrong, arranged by what you notice rather than by where it
runs. How to bring the service up in the first place is elsewhere: [README](../README.md)
for Compose, [kubernetes.md](kubernetes.md) for kind, [aws.md](aws.md) for EKS. Each of
those has an "Operating notes" section about running on that platform; this is about
symptoms.

Every command here assumes the API is reachable and `$KEY` holds `API_KEY`. On Kubernetes,
reach it with `kubectl -n groundwork port-forward svc/api 8000:8000` first.

## Start here

Two endpoints separate "this process is broken" from "something it depends on is":

```bash
curl -s localhost:8000/health/live     # the process answers
curl -s localhost:8000/health/ready    # and its database is reachable
```

`/health/live` needs no dependency, so a pod failing it is wedged or still loading the
embedding model. `/health/ready` returns **503** with `{"checks": {"database": "unavailable"}}`
when the database cannot be reached, which is deliberate: traffic stops routing to that pod
rather than being served errors.

Logs are JSON on stdout. `docker compose logs api worker`, or
`kubectl -n groundwork logs deploy/api`.

## The API returns 503

The database is unreachable. `/health/ready` names which check failed.

- **Is the database up?** `docker compose ps postgres`, or `kubectl -n groundwork get pods`.
  On EKS the instance is in the default VPC with no public address; see [aws.md](aws.md).
- **Connections time out after 5 seconds**, not psycopg's default 130 (`database_connect_timeout_seconds`).
  A request that hangs far longer than that is not waiting on the database.
- Readiness recovers on its own once the database answers. Nothing needs restarting.

## The API will not start

- **`ANTHROPIC_API_KEY` missing while `GENERATOR=anthropic`.** The default generator is the
  real model, and it refuses to start rather than serve fake answers. Either set a key or
  set `GENERATOR=stub`. `.env.example` ships the stub, so a fresh clone comes up without a
  key; a deployment that sets neither is meant to fail here.
- **`API_KEY` missing.** The API refuses to start without one. The worker and the eval
  harness do not read it.
- **`S3_ACCESS_KEY` or `S3_SECRET_KEY` missing while `S3_ENDPOINT` is set.** Both are
  required whenever an endpoint is given, which is how MinIO is addressed. On AWS the
  endpoint is empty and both are unset, so boto3 uses the pod's role.

## A document never finishes indexing

Ask the job what happened. Its status is one of `queued`, `running`, `completed`, `failed`:

```bash
curl -s localhost:8000/jobs/<job id> -H "x-api-key: $KEY"
```

- **`queued` and staying there** means no worker is claiming it. Check a worker is running
  and not crash-looping. On Compose, `docker compose ps worker`.
- **`running` for a long time** is normal for a large document; the worker heartbeats while
  it works. A claim whose heartbeat stops for **5 minutes** (`job_stale_after_seconds`)
  becomes claimable again, so a crashed worker's job is picked up by another.
- **`failed` with `abandoned: no worker finished it in 3 attempts`** means the job was
  reclaimed to its ceiling (`job_max_attempts`). A document that reliably kills its worker
  stops cycling rather than retrying forever. The error is recorded on the job.
- **`failed` with something else** carries the exception class, never its message, which
  could hold document content.

Once the cause is fixed, ingest it again without re-uploading:

```bash
curl -s -X POST localhost:8000/documents/<document id>/reindex -H "x-api-key: $KEY"
```

There is no automatic retry with backoff; it is parked in the roadmap, deliberately, since
requeueing without backoff spends every attempt in seconds during an outage.

## The worker looks wedged

The worker serves no HTTP, so on Kubernetes its liveness is a file it touches at the start
of every pass and at every job heartbeat: `WORKER_LIVENESS_FILE`, `/tmp/worker-alive`, whose
probe fails when it is older than 120 seconds. The setting has no default, so nothing is
written where it is unset, as under Compose.

- **It touches the file on failed passes too**, so a worker riding out a database outage
  does not look stuck and is not restarted. That is intended: restarting every worker
  during a database outage is the failure the design avoids.
- **A restart loop on Kubernetes** is a probe failing for real. `kubectl -n groundwork
  describe pod <pod>` shows which, and `logs --previous` shows what it said before dying.

## Uploads fail

- **`NoSuchBucket`** means the bucket was never created. Compose runs `createbucket` as a
  one-shot inside `docker compose up`, and Kubernetes as a Job; on a fresh volume it must
  have completed. `kubectl -n groundwork get jobs`.
- **413** means the file is over `max_upload_bytes`, 25 MiB by default. Uploads are held in
  memory while hashing, so the cap is deliberate.
- **A scanned PDF fails its job** with `no extractable text; scanned and image-only PDFs are
  out of scope`. Only text-native PDFs are in scope, there is no OCR, and the job says so
  rather than indexing nothing silently. A file that will not open fails with
  `could not read pdf`.

## Questions come back without an answer

`outcome` says which, and they mean different things:

- **`insufficient_evidence`** — the model declined to answer from what was retrieved. This
  is the refusal path working, not a fault. If it happens for a question the corpus does
  answer, the problem is retrieval, not generation.
- **`declined`** — the model refused the request itself.
- **`failed`** — generation errored or passed `generation_timeout_seconds`, 60 by default.

Answers with a stub-looking body are the stub generator: check `GENERATOR`.

## Rotating the API key

The key is a single shared value, not a per-tenant table — a hashed key table is parked in
the roadmap.

- **Compose:** change `API_KEY` in `.env` and `docker compose up -d` to recreate the API.
- **Kubernetes:** change it in the gitignored `secrets.env` and apply. The Kustomize
  `secretGenerator` names the Secret by a hash of its contents, so the name changes and the
  pods roll on their own. Applying without the file fails rather than deploying a stale key.

## CI went red without a code change

Something outside the repository moved. This is why CI runs on a schedule as well as on
pushes: MinIO's Docker Hub images were deleted between two runs on 2026-09-11 and surfaced
only because a docs commit happened to follow.

- Re-run it after fixing the outside cause with the `workflow_dispatch` trigger on `ci.yml`;
  no empty commit is needed.
- **A pinned digest that no longer resolves** is the likeliest cause. Check them:
  `docker buildx imagetools inspect <ref>` for each pinned image.
- GitHub disables scheduled workflows after 60 days without repository activity in a
  **public** repository, which is what its documentation covers. This repository is private,
  so the rule as documented does not reach it — but if the nightly simply stops appearing,
  check that first.

## Images and the registry

- **ECR exists only while `infra/data` does**, which is destroyed with the cluster. Pushing
  when nothing is deployed will fail, and that is expected rather than a fault.
- **Tags are immutable and name a commit.** A tag already pushed is left alone; both
  `make ecr-images` and the `images.yml` workflow check before building.
- **Pushing from CI** is the `push_to_ecr` input on `images.yml`, authenticating through
  GitHub's OIDC provider. It needs the `AWS_ROLE_ARN` repository secret, which is
  `infra/data`'s `github_actions_role_arn` output.
- **Scan findings** live in ECR: `aws ecr describe-image-scan-findings --repository-name
  groundwork-api --image-id imageTag=<commit>`. Both images carry findings in Debian 12
  packages from the base image; moving to Debian 13 is parked.

## Traps

Failures that mislead, each met at least once here.

- **Prune the builder Compose actually uses, by name.** This Mac has two, `default` and
  `desktop-linux`. On 2026-09-18 `default` held no cache while `desktop-linux` held all 222
  records, so an unqualified prune could have emptied the wrong one and reported success:
  `docker buildx prune -af --builder desktop-linux`.
- **A finished Job cannot be changed.** Applying a Kubernetes overlay with new images is
  refused for a completed `migrate` Job with `field is immutable`. Delete the Job and apply
  again.
- **This Mac's DNS is intercepted**, and its router cached a former delegation for hours.
  Check names over DoH or the Route 53 API rather than trusting a local answer; see
  [aws.md](aws.md).
- **Some networks accept connections on port 80 to any address**, which makes a closed port
  look open. The AWS smoke test refuses to run on such a network for that reason.
- **On macOS `pytest` occasionally exits 134** after every test has passed, printing a
  `recursive_mutex lock failed` as the interpreter shuts down. Gate on the exit code, and
  when it is 134 after a clean summary, rerun rather than treat it as a regression.
- **Stop the worker before running integration tests.** A running worker claims the jobs the
  tests create, and they fail unpredictably.
