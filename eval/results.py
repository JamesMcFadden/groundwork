"""What a run records: the figures, each question's detail, and how to reproduce them.

Every figure carries its n, as success-criteria.md requires, and every run records the
commit, corpus, golden set, model, chunking, and pgvector version it was measured with.
"""

import json
import statistics
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ingest.chunk import CHUNK_TOKENS, OVERLAP_TOKENS
from app.services.embeddings import EMBEDDING_MODEL
from eval.corpus import Corpus
from eval.golden import GoldenSet
from eval.retrieval import MRR_K, RECALL_K, RetrievalReport

RESULTS_DIR = Path(__file__).parent / "results"


def run_metadata(
    session: Session, corpus: Corpus, golden: GoldenSet, started: datetime
) -> dict[str, Any]:
    return {
        "started_at": started.isoformat(timespec="seconds"),
        "git": _git_state(),
        "corpus_manifest_sha256": corpus.manifest_sha256,
        "golden_set_sha256": golden.sha256,
        "embedding_model": EMBEDDING_MODEL,
        "chunk_tokens": CHUNK_TOKENS,
        "chunk_overlap_tokens": OVERLAP_TOKENS,
        "retriever": "dense",
        "pgvector_version": pgvector_version(session),
    }


def pgvector_version(session: Session) -> str | None:
    """The installed extension's version: index-scan behaviour changes between releases."""
    version: str | None = session.scalar(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )
    return version


def retrieval_record(report: RetrievalReport) -> dict[str, Any]:
    return {
        "recall_at_5": report.recall_at_5,
        "hits_at_5": report.hits_at_5,
        "mrr_at_10": report.mrr_at_10,
        "answerable_count": len(report.answerable),
        "answerable": [asdict(result) for result in report.answerable],
        "unanswerable": [asdict(result) for result in report.unanswerable],
    }


def write_results(record: Mapping[str, Any], directory: Path, started: datetime) -> Path:
    """Write a run's record as JSON, named by when it started, and return the path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    return path


def render_summary(
    report: RetrievalReport, metadata: Mapping[str, Any], chunk_counts: Mapping[str, int]
) -> str:
    """A Markdown summary of the run, for a terminal or a CI job summary."""
    git = metadata["git"]
    commit = "unknown commit" if git is None else git["commit"][:7]
    if git is not None and git["uncommitted_changes"]:
        commit += " with uncommitted changes"
    n = len(report.answerable)

    lines = [
        "## Retrieval evaluation (dense)",
        "",
        f"Commit {commit}; corpus `{metadata['corpus_manifest_sha256'][:12]}` "
        f"({len(chunk_counts)} documents, {sum(chunk_counts.values())} chunks); golden set "
        f"`{metadata['golden_set_sha256'][:12]}` ({n} answerable, "
        f"{len(report.unanswerable)} unanswerable).",
        "",
        "| Figure | Value |",
        "| --- | --- |",
        f"| Recall@{RECALL_K} | {report.hits_at_5}/{n} = {report.recall_at_5:.3f} |",
        f"| MRR@{MRR_K} (n={n}) | {report.mrr_at_10:.3f} |",
        "",
        _misses(report),
        "",
        "Best chunk score: answerable "
        + _spread([result.best_score for result in report.answerable])
        + "; unanswerable "
        + _spread([result.best_score for result in report.unanswerable])
        + ".",
    ]
    return "\n".join(lines)


def _misses(report: RetrievalReport) -> str:
    missed = [
        f"`{result.question_id}` ("
        + (
            f"rank {result.rank_at_10} of {MRR_K}"
            if result.rank_at_10 is not None
            else f"not in top {MRR_K}"
        )
        + ")"
        for result in report.answerable
        if result.rank_at_5 is None
    ]
    return f"Missed at {RECALL_K}: " + (", ".join(missed) if missed else "none") + "."


def _spread(scores: Sequence[float | None]) -> str:
    present = [score for score in scores if score is not None]
    if not present:
        return "none"
    return (
        f"min {min(present):.3f}, median {statistics.median(present):.3f}, "
        f"max {max(present):.3f} (n={len(present)})"
    )


def _git_state() -> dict[str, Any] | None:
    """The commit measured, and whether tracked files had changed since it."""
    try:
        commit = _git("rev-parse", "HEAD").strip()
        # Untracked files cannot change what runs, so only tracked changes count.
        changes = _git("status", "--porcelain", "--untracked-files=no").strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return {"commit": commit, "uncommitted_changes": bool(changes)}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=Path(__file__).parent, capture_output=True, text=True, check=True
    ).stdout
