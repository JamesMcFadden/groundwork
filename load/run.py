"""One measured load run against the kind deployment, or a wiring check.

    uv run python -m load.run --replicas 3 --label scaling-3
    uv run python -m load.run --replicas 3 --label recovery-1 --delete-pod-at 120
    uv run python -m load.run --replicas 3 --wiring

A run scales the worker to 0 and the API to the replicas asked for, and waits until only
those API pods are running and ready. It then runs a one-minute warm-up, whose figures are
discarded, and the measured run: 30 virtual users for 5 minutes, as docs/roadmap.md fixed
before any run. `--delete-pod-at` deletes the API pod first in name order that many seconds
into the measured run. The record goes to load/results/, and measured runs refuse images
built from uncommitted changes.

A wiring check runs 2 users for 10 seconds with no warm-up, prints the statuses seen and the
failure counts only, and records nothing, so no throughput or latency is seen before the
rules apply.
"""

import argparse
import json
import re
import subprocess
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from load.cluster import CONTEXT, NAMESPACE, kubectl

LOAD_DIR = Path(__file__).parent
RESULTS_DIR = LOAD_DIR / "results"
JOB_TEMPLATE = LOAD_DIR / "k6-job.yaml"
SCRIPT = LOAD_DIR / "questions.js"
SUMMARY_BEGIN = "===K6-SUMMARY==="
SUMMARY_END = "===END-K6-SUMMARY==="
NODE_CONTAINER = "groundwork-control-plane"

# Fixed in docs/roadmap.md before any run.
VUS = 30
DURATION = "5m"
WARMUP = "1m"
WIRING_VUS = 2
WIRING_DURATION = "10s"


@dataclass
class PodTally:
    """What one API pod logged about questions during a run."""

    requests: int = 0
    statuses: Counter[int] = field(default_factory=Counter)
    database_unavailable: int = 0


@dataclass
class JobRun:
    summary: dict[str, Any]
    started: datetime
    ended: datetime
    pod_logs: dict[str, list[str]]
    deleted: dict[str, Any] | None


def extract_summary(logs: str) -> dict[str, Any]:
    """The summary the k6 script printed between its markers."""
    start = logs.find(SUMMARY_BEGIN)
    end = logs.find(SUMMARY_END, start)
    if start < 0 or end < 0:
        raise ValueError("no k6 summary in the job's log")
    summary: dict[str, Any] = json.loads(logs[start + len(SUMMARY_BEGIN) : end])
    return summary


def counts(summary: dict[str, Any]) -> dict[str, int]:
    """Requests sent, and the classes the criteria count. A counter k6 left out is zero."""

    def count(name: str) -> int:
        return int(summary["metrics"].get(name, {}).get("values", {}).get("count", 0))

    return {
        name: count(name)
        for name in (
            "http_reqs",
            "responses_5xx",
            "responses_not_201",
            "requests_without_response",
        )
    }


def throughput(summary: dict[str, Any]) -> float:
    """Completed requests, those that got a response, per second of the test run."""
    tally = counts(summary)
    completed = tally["http_reqs"] - tally["requests_without_response"]
    seconds = float(summary["state"]["testRunDurationMs"]) / 1000
    return completed / seconds


def tally_pod_log(lines: list[str], start: datetime, end: datetime) -> PodTally:
    """Count one API pod's question requests, by status, and its 503 warnings, in a window."""
    tally = PodTally()
    for line in lines:
        if not line.startswith("{"):
            continue
        record = json.loads(line)
        if not start <= datetime.fromisoformat(record["time"]) <= end:
            continue
        if record.get("logger") == "app.access" and record.get("route") == "/questions":
            tally.requests += 1
            tally.statuses[int(record["status"])] += 1
        elif (
            record.get("logger") == "app.api.errors" and record["message"] == "database unavailable"
        ):
            tally.database_unavailable += 1
    return tally


def render_job(template: str, *, name: str, vus: int, duration: str, secret: str) -> str:
    values = {"${NAME}": name, "${VUS}": str(vus), "${DURATION}": duration, "${SECRET}": secret}
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    if "${" in template:
        raise ValueError("the job template has a placeholder this run does not fill")
    return template


def docker_format(target: str, template: str) -> str:
    return subprocess.run(
        ["docker", "inspect", target, "--format", template],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def pods(app: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = json.loads(
        kubectl("get", "pods", "-l", f"app={app}", "-o", "json")
    )["items"]
    return items


def settle(replicas: int) -> None:
    """Scale the worker to 0 and the API to `replicas`, and wait until nothing else runs."""
    kubectl("scale", "deployment/worker", "--replicas=0")
    kubectl("scale", "deployment/api", f"--replicas={replicas}")
    kubectl("rollout", "status", "deployment/api", "--timeout=300s")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        api = pods("api")
        ready = [
            pod
            for pod in api
            if not pod["metadata"].get("deletionTimestamp")
            and all(s.get("ready") for s in pod["status"].get("containerStatuses", [{}]))
        ]
        if len(api) == len(ready) == replicas and not pods("worker"):
            return
        time.sleep(2)
    raise SystemExit(f"the API did not settle at {replicas} ready pods with the worker stopped")


def job_state(name: str) -> str | None:
    status = json.loads(kubectl("get", "job", name, "-o", "json"))["status"]
    if status.get("succeeded"):
        return "succeeded"
    if status.get("failed"):
        return "failed"
    return None


def wait_until_running(name: str) -> datetime:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        items = json.loads(
            kubectl("get", "pods", "-l", f"batch.kubernetes.io/job-name={name}", "-o", "json")
        )["items"]
        if items and items[0]["status"].get("phase") in ("Running", "Succeeded", "Failed"):
            return datetime.now(UTC)
        time.sleep(1)
    raise SystemExit(f"job {name} never started")


def run_job(name: str, *, vus: int, duration: str, delete_pod_at: float | None = None) -> JobRun:
    """Run k6 once, collecting its summary and every API pod's log from the run."""
    deployment = json.loads(kubectl("get", "deployment", "api", "-o", "json"))
    sources = deployment["spec"]["template"]["spec"]["containers"][0]["envFrom"]
    secret = next(s["secretRef"]["name"] for s in sources if "secretRef" in s)
    job = render_job(JOB_TEMPLATE.read_text(), name=name, vus=vus, duration=duration, secret=secret)

    with tempfile.TemporaryDirectory() as directory:
        streams: dict[str, tuple[subprocess.Popen[bytes], Path]] = {}
        for pod in sorted(p["metadata"]["name"] for p in pods("api")):
            path = Path(directory) / f"{pod}.log"
            with path.open("wb") as output:
                command = ["kubectl", "--context", CONTEXT, "-n", NAMESPACE, "logs", "-f", pod]
                streams[pod] = (subprocess.Popen(command, stdout=output), path)

        kubectl("create", "-f", "-", stdin=job)
        started = wait_until_running(name)
        deleted: dict[str, Any] | None = None
        while (state := job_state(name)) is None:
            elapsed = (datetime.now(UTC) - started).total_seconds()
            if delete_pod_at is not None and deleted is None and elapsed >= delete_pod_at:
                target = sorted(streams)[0]
                kubectl("delete", "pod", target, "--wait=false")
                deleted = {"pod": target, "seconds_into_run": round(elapsed, 1)}
            time.sleep(1)
        ended = datetime.now(UTC)

        time.sleep(2)
        pod_logs: dict[str, list[str]] = {}
        for pod, (process, path) in streams.items():
            process.kill()
            process.wait()
            pod_logs[pod] = path.read_text().splitlines()
        for pod in (p["metadata"]["name"] for p in pods("api")):
            if pod not in pod_logs:
                pod_logs[pod] = kubectl("logs", pod).splitlines()

    logs = kubectl("logs", f"job/{name}")
    if state != "succeeded":
        raise SystemExit(f"k6 job {name} failed:\n{logs[-2000:]}")
    return JobRun(extract_summary(logs), started, ended, pod_logs, deleted)


def question_timings(collection_id: str, start: datetime, end: datetime) -> dict[str, Any]:
    """P95 of total_ms − llm_ms over the questions recorded in the run's window."""
    sql = (
        "SELECT count(*), percentile_cont(0.95) WITHIN GROUP "
        "(ORDER BY total_ms - COALESCE(llm_ms, 0)) FROM questions "
        f"WHERE collection_id = '{uuid.UUID(collection_id)}' "
        f"AND created_at >= '{start.isoformat()}' AND created_at <= '{end.isoformat()}'"
    )
    script = 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F, -c "$1"'
    row = kubectl("exec", "postgres-0", "--", "sh", "-c", script, "sh", sql)
    count, p95 = row.split(",")
    return {"rows": int(count), "p95_total_minus_llm_ms": float(p95) if p95 else None}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--replicas", type=int, choices=(1, 3), required=True)
    parser.add_argument("--label", help="names the record, such as scaling-1 or recovery-2")
    parser.add_argument("--delete-pod-at", type=float, metavar="SECONDS")
    parser.add_argument("--wiring", action="store_true")
    args = parser.parse_args()
    if not args.wiring and not re.fullmatch(r"[a-z0-9-]{1,30}", args.label or ""):
        parser.error("a measured run needs --label of lowercase letters, digits, and hyphens")

    revision = docker_format(
        "groundwork-api:kind", '{{index .Config.Labels "org.opencontainers.image.revision"}}'
    )
    if not args.wiring and revision.endswith("-dirty"):
        raise SystemExit(f"images are {revision}: build them from a clean commit for a record")

    settle(args.replicas)
    script = kubectl(
        "create", "configmap", "k6-script", f"--from-file=questions.js={SCRIPT}",
        "--dry-run=client", "-o", "yaml",
    )  # fmt: skip
    kubectl("apply", "-f", "-", stdin=script)
    stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")

    if args.wiring:
        run = run_job(
            f"k6-wiring-{stamp}",
            vus=WIRING_VUS,
            duration=WIRING_DURATION,
            delete_pod_at=args.delete_pod_at,
        )
        statuses = sorted(
            {
                s
                for lines in run.pod_logs.values()
                for s in tally_pod_log(lines, run.started, run.ended).statuses
            }
        )
        failures = {k: v for k, v in counts(run.summary).items() if k != "http_reqs"}
        print(f"wiring at {args.replicas} replica(s): statuses seen {statuses}, {failures}")
        print(f"deleted: {run.deleted}")
        return

    run_job(f"k6-warmup-{stamp}", vus=VUS, duration=WARMUP)
    run = run_job(
        f"k6-{args.label}-{stamp}", vus=VUS, duration=DURATION, delete_pod_at=args.delete_pod_at
    )
    seeded = json.loads(
        kubectl("get", "configmap", "load-input", "-o", "jsonpath={.data.input\\.json}")
    )
    restarts = {
        pod["metadata"]["name"]: pod["status"]["containerStatuses"][0]["restartCount"]
        for pod in pods("api")
    }
    record = {
        "label": args.label,
        "replicas": args.replicas,
        "vus": VUS,
        "duration": DURATION,
        "warmup": WARMUP,
        "started_at": run.started.isoformat(),
        "ended_at": run.ended.isoformat(),
        "api_image_revision": revision,
        "node_image": docker_format(NODE_CONTAINER, "{{.Config.Image}}"),
        "k6_image": re.search(r"image: (grafana/k6\S+)", JOB_TEMPLATE.read_text()).group(1),  # type: ignore[union-attr]
        "collection": seeded["collection_name"],
        "corpus_manifest_sha256": seeded["corpus_manifest_sha256"],
        "deleted": run.deleted,
        "counts": counts(run.summary),
        "throughput_per_second": throughput(run.summary),
        "http_req_duration_ms": run.summary["metrics"]["http_req_duration"]["values"],
        "pods": {
            pod: {
                "requests": tally.requests,
                "statuses": dict(tally.statuses),
                "database_unavailable_warnings": tally.database_unavailable,
                "restarts": restarts.get(pod),
            }
            for pod, tally in (
                (pod, tally_pod_log(lines, run.started, run.ended))
                for pod, lines in run.pod_logs.items()
            )
        },
        "questions": question_timings(seeded["collection_id"], run.started, run.ended),
        "k6_summary": run.summary,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{stamp.upper()}-{args.label}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    print(f"recorded {path}")


if __name__ == "__main__":
    main()
