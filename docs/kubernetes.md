# Kubernetes

The service on a local Kubernetes cluster: what runs there, how to bring it up from
nothing, and how the load tests that measured it are run. The results, and the rules they
were judged by, are in [roadmap.md](roadmap.md#m6--kubernetes-on-kind); the criteria are
in [success-criteria.md](success-criteria.md).

The cluster is [kind](https://kind.sigs.k8s.io/): Kubernetes in a Docker container, on the
same Docker VM Compose uses. It proves the deployment and the scaling and recovery
behaviour, not production capacity: every pod shares that VM's CPUs and memory.

## What runs

Everything lives in the `groundwork` namespace of a one-node cluster, from the manifests
in `k8s/`, applied together with `kubectl apply -k k8s`.

| Workload | Kind | Role |
| --- | --- | --- |
| `api` | Deployment, 3 replicas, Service `api:8000` | The API, one uvicorn process per pod, answering with the stub generator |
| `worker` | Deployment, 1 replica | Ingestion |
| `postgres` | StatefulSet, 2 Gi volume, Service `postgres:5432` | PostgreSQL with pgvector, standing in for RDS |
| `minio` | StatefulSet, 2 Gi volume, Service `minio:9000` | MinIO, standing in for S3 |
| `migrate` | Job | `alembic upgrade head` |
| `create-bucket` | Job | Creates the documents bucket |

PostgreSQL and MinIO run the images Compose and CI pin, by the same digests. The API and
the worker run images built from the working tree and loaded into the node, tagged `kind`
and never pulled; each carries the commit it was built from as its
`org.opencontainers.image.revision` label, with `-dirty` for uncommitted changes.

**Configuration.** Settings that are not secret come from the generated ConfigMap
`groundwork-config`, and `POSTGRES_PASSWORD`, `S3_SECRET_KEY`, and `API_KEY` from the
Secret `groundwork-secrets`, generated from the gitignored `k8s/secrets.env`. Their
generated names change with their content, so a changed value rolls the pods that read
it. The API sets `GENERATOR=stub`, and the cluster holds no `ANTHROPIC_API_KEY`: load
tests measure this service, not a model provider. Every pod sets
`enableServiceLinks: false`: Kubernetes would otherwise give each pod variables such as
`POSTGRES_PORT=tcp://10.96.0.1:5432` for every Service in the namespace, which the
application's settings read, and refuse, as the database port.

**Probes.**

- The API's startup and liveness probes read `/health/live`, which checks nothing
  external, so a database outage never restarts it; readiness reads `/health/ready`,
  which fails while the database is unreachable and takes the pod out of the Service.
- The worker serves no HTTP. Its startup and liveness probes check that
  `/tmp/worker-alive` is less than 120 seconds old; the worker touches that file at the
  start of every pass, a failed one included, and before every embedding batch. The file
  says nothing about the database, so an outage never restarts the worker, while a
  worker stuck mid-job is restarted once the file goes stale.
- PostgreSQL and MinIO have readiness probes only: a database slow under load must not be
  restarted in the middle of a measurement.

**Resources.**

| Workload | CPU request | CPU limit | Memory request | Memory limit |
| --- | --- | --- | --- | --- |
| `api` | 1 | 1 | 512 Mi | 1 Gi |
| `worker` | 1 | 1 | 1.5 Gi | 2 Gi |
| `postgres` | 2 | none | 1 Gi | 2 Gi |
| `minio` | 250m | none | 256 Mi | 1 Gi |

One CPU per API pod, requested and limited, makes replicas the unit of scaling; without a
limit, one pod could use the whole node. `EMBEDDING_THREADS=1` matches it: left to choose,
ONNX Runtime sizes its thread pools from the node's CPUs, and a one-CPU limit then
throttled query embedding from about 7 ms to 104 ms at P50. PostgreSQL has no CPU limit,
so it is never the throttled component. Memory was set from peaks measured in the pods:
326 MiB for an API pod under 30 concurrent questions, and 1,402 MiB for the worker
ingesting a 434-page document near the 25 MiB upload cap.

**Stopping an API pod.** Deleting a pod takes it out of its Service, but a connection a
client already holds keeps reaching it, and uvicorn closes idle connections the moment it
stops. So the API's preStop hook runs `kill -USR1 1; sleep 10` first. SIGUSR1 starts
draining: every response then carries `Connection: close`, and clients reconnect through
the Service to other pods while this one still serves. SIGTERM follows 10 seconds later,
within the default 30-second grace period.

**Unreachable database.** Every attempt to connect gives up after
`DATABASE_CONNECT_TIMEOUT_SECONDS`, 5 by default. psycopg's own bound is 130 seconds,
which with PostgreSQL stopped held the worker's first poll long enough to age its
liveness file past the probe's threshold.

## Bringing it up

Needs Docker Desktop, [uv](https://docs.astral.sh/uv/), `openssl`, and a `kubectl` within
one minor version of Kubernetes 1.34; the one Docker Desktop ships, 1.34.1, is. The
commands run from the repository's root.

1. **Install kind** v0.33.0 from its release binary, checking it against the published
   checksum. For Apple Silicon:

   ```bash
   curl -fsSLo kind https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-darwin-arm64
   echo "0c8c7dbe5e23594a198b786c4bc13dacc101fa6196b0cb0b23a1ca44e61f4b4f  kind" | shasum -a 256 -c
   chmod +x kind && mv kind ~/.local/bin/
   ```

   Other platforms use the release's other binaries, each with a `.sha256sum` beside it.

2. **Create the cluster.** `k8s/kind-cluster.yaml` pins the node image by digest, to
   Kubernetes 1.34 rather than kind's default of 1.37, and names kube-proxy's mode. kind
   switches `kubectl` to the new cluster's context, `kind-groundwork`.

   ```bash
   kind create cluster --config k8s/kind-cluster.yaml
   ```

3. **Write the secrets**, which never leave this machine:

   ```bash
   (umask 077; for name in POSTGRES_PASSWORD S3_SECRET_KEY API_KEY; do
     echo "$name=$(openssl rand -hex 32)"; done > k8s/secrets.env)
   ```

4. **Build the images and load them into the node.** Until they are loaded, the API, the
   worker, and the Jobs fail to start with `ErrImageNeverPull`.

   ```bash
   make kind-images
   ```

5. **Apply the manifests, and wait for the Jobs and the API.** The Jobs fail and retry
   until PostgreSQL and MinIO accept connections, so they can take a minute or two; on a
   fresh cluster the migration failed three times and bucket creation once before each
   completed.

   ```bash
   kubectl apply -k k8s
   kubectl -n groundwork wait --for=condition=complete job/migrate job/create-bucket --timeout=10m
   kubectl -n groundwork rollout status deployment/api deployment/worker --timeout=5m
   ```

6. **Reach the API** through a port-forward, from a second terminal, with the key from
   `k8s/secrets.env`. Port 8000 must be free, so stop Compose's `api` first if it runs.

   ```bash
   kubectl -n groundwork port-forward svc/api 8000:8000
   ```

   ```bash
   KEY=$(grep '^API_KEY=' k8s/secrets.env | cut -d= -f2)
   curl -s -X POST localhost:8000/collections -H "x-api-key: $KEY" \
     -H 'content-type: application/json' -d '{"name": "demo"}'
   ```

   A port-forward sends everything to one pod. The load tests run inside the cluster
   instead, against the Service, so that kube-proxy spreads connections across the pods.

To remove everything, cluster and volumes included:

```bash
kind delete cluster --name groundwork
```

## Load tests

The load tests answer from the evaluation corpus, seeded through the API so the worker in
the cluster indexes it. Seeding creates a new collection and writes its id, with the
golden set's 38 questions, into the `load-input` ConfigMap:

```bash
uv run python -m load.seed
```

`load/run.py` runs k6 as a Job inside the cluster, from `load/questions.js` and the
template `load/k6-job.yaml`, which the manifests leave out so that applying them never
starts a run. Each run scales the worker to 0 and the API to the replicas asked for, waits
until only those pods run, then runs a one-minute warm-up whose figures are discarded and
the measured run: 30 virtual users for 5 minutes, posting the questions in turn with no
pause and keeping their connections. Its record, with the k6 summary, the counts the
criteria use, each API pod's requests, 503 warnings, and restarts, and the P95 of
`total_ms − llm_ms` over the run's questions, goes to `load/results/`.

A wiring check runs 2 users for 10 seconds, prints only the statuses seen and the failure
counts, and records nothing. It confirms a deployment works without showing a throughput
or latency figure before the rules for a comparison are fixed:

```bash
uv run python -m load.run --replicas 3 --wiring
uv run python -m load.run --replicas 3 --wiring --delete-pod-at 5
```

A measured run needs images built from a clean commit, and refuses a `-dirty` label. For
the runs recorded in M6, the images were rebuilt from a clean commit and the corpus seeded
again with them first:

```bash
make kind-images
kubectl -n groundwork rollout restart deployment/api deployment/worker
uv run python -m load.seed
```

Then the runs, in this order:

```bash
uv run python -m load.run --replicas 1 --label scaling-1-a
uv run python -m load.run --replicas 3 --label scaling-3-a
uv run python -m load.run --replicas 1 --label scaling-1-b
uv run python -m load.run --replicas 3 --label scaling-3-b
uv run python -m load.run --replicas 1 --label scaling-1-c
uv run python -m load.run --replicas 3 --label scaling-3-c
uv run python -m load.run --replicas 3 --label recovery-1 --delete-pod-at 120
uv run python -m load.run --replicas 3 --label recovery-2 --delete-pod-at 120
uv run python -m load.run --replicas 3 --label recovery-3 --delete-pod-at 120
```

Each takes about six minutes, and all nine load most of the machine's CPUs for an hour, so
other heavy work then skews the figures.

## Operating notes

- **Rebuilt images keep the tag `kind`**, so pods do not pick them up by themselves: after
  `make kind-images`, run `kubectl -n groundwork rollout restart deployment/api
  deployment/worker`.
- **A finished Job is not run again** by applying the manifests. To rerun migrations or
  bucket creation, delete the Job and apply again. A changed ConfigMap or Secret means
  deleting both Jobs before applying, since a Job's pod template cannot change and its
  references to them would.
- **`kubectl apply -k k8s` resets the API to 3 replicas** and the worker to 1, undoing a
  `kubectl scale` or a load run's scaling. `load.seed` scales the worker back to 1 itself.
- **Deleting the namespace** with `kubectl delete namespace groundwork` removes the whole
  deployment, volumes included, and leaves the cluster.
