"""What a run records: the figures, each question's detail, and how to reproduce them.

Every figure carries its n, as success-criteria.md requires, and every run records the
commit, corpus, golden set, models, chunking, and pgvector version it was measured with.
"""

import json
import statistics
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.generation.claude import MODEL as CLAUDE_MODEL
from app.ingest.chunk import CHUNK_TOKENS, OVERLAP_TOKENS
from app.services.embeddings import EMBEDDING_MODEL
from eval.answering import STAGES, AnsweringReport
from eval.corpus import Corpus
from eval.golden import GoldenSet
from eval.prefix import MIN_NET_FIXED, QUERY_INSTRUCTION, PrefixComparison
from eval.retrieval import MRR_K, RECALL_K, RetrievalReport

RESULTS_DIR = Path(__file__).parent / "results"


def run_metadata(
    session: Session, corpus: Corpus, golden: GoldenSet, started: datetime, answers: str
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
        "answers": answers,
        "generation_model": CLAUDE_MODEL if answers == "claude" else None,
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


def prefix_record(comparison: PrefixComparison) -> dict[str, Any]:
    return {
        "prefix": QUERY_INSTRUCTION,
        "min_net_fixed": MIN_NET_FIXED,
        "fixed_at_5": comparison.fixed,
        "broken_at_5": comparison.broken,
        "net_fixed": comparison.net_fixed,
        "adopt": comparison.adopt,
        "without": retrieval_record(comparison.without),
        "with_prefix": retrieval_record(comparison.with_prefix),
    }


def answering_record(report: AnsweringReport, measured: bool) -> dict[str, Any]:
    return {
        "measured": measured,
        "refused": report.refused,
        "unanswerable_count": len(report.unanswerable),
        "false_refusals": report.false_refusals,
        "answerable_count": len(report.answerable),
        "outcomes": dict(Counter(result.outcome for result in report.results)),
        "markers_checked": report.markers_checked,
        "unresolved_markers": report.unresolved_markers,
        "questions_checked": report.questions_checked,
        "rejected_citations": report.rejected_citations,
        "accepted_citations": report.accepted_citations,
        "raw_invalid_citation_rate": report.raw_invalid_citation_rate,
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
        "latency_ms": {stage: report.latency(stage) for stage in STAGES},
        "questions": [
            asdict(result) | {"unresolved_markers": list(result.unresolved_markers)}
            for result in report.results
        ],
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
    """A Markdown summary of the retrieval figures, for a terminal or a CI job summary."""
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


def render_prefix(comparison: PrefixComparison) -> str:
    """The pre-registered prefix comparison and its verdict, as Markdown."""
    without, with_prefix = comparison.without, comparison.with_prefix
    n = len(without.answerable)
    decision = "adopt the prefix" if comparison.adopt else "keep questions unprefixed"
    return "\n".join(
        [
            "## Query instruction prefix (pre-registered comparison)",
            "",
            f"`{QUERY_INSTRUCTION}` added in front of questions only, over the same {n} "
            "answerable questions.",
            "",
            "| Figure | Without | With |",
            "| --- | --- | --- |",
            f"| Recall@{RECALL_K} | {without.hits_at_5}/{n} = {without.recall_at_5:.3f} "
            f"| {with_prefix.hits_at_5}/{n} = {with_prefix.recall_at_5:.3f} |",
            f"| MRR@{MRR_K} | {without.mrr_at_10:.3f} | {with_prefix.mrr_at_10:.3f} |",
            "",
            f"Fixed at {RECALL_K}: {_ids(comparison.fixed)}. Broken at {RECALL_K}: "
            f"{_ids(comparison.broken)}. Net {comparison.net_fixed:+d}.",
            "",
            f"Rule: adopt only if it fixes at least {MIN_NET_FIXED} more hits than it breaks "
            f"and MRR@{MRR_K} does not fall. Decision: {decision}.",
        ]
    )


def _ids(question_ids: Sequence[str]) -> str:
    return ", ".join(f"`{question_id}`" for question_id in question_ids) or "none"


def render_answering(report: AnsweringReport, measured: bool, model: str | None) -> str:
    """A Markdown summary of the answering figures, or why they were not measured."""
    n = len(report.results)
    outcomes = ", ".join(
        f"{outcome} {count}"
        for outcome, count in sorted(Counter(r.outcome for r in report.results).items())
    )
    if not measured:
        return "\n".join(
            [
                "## Answering evaluation",
                "",
                "Not measured: the stub generator answered, and it answers every question from "
                "its top passage. Run `make eval-live` to measure with Claude.",
                "",
                f"The answering path ran for all {n} questions ({outcomes}).",
            ]
        )

    rate = report.raw_invalid_citation_rate
    total = report.rejected_citations + report.accepted_citations
    rate_text = (
        "no citations checked"
        if rate is None
        else f"{report.rejected_citations}/{total} = {rate:.3f} "
        f"({report.questions_checked} questions checked)"
    )
    lines = [
        f"## Answering evaluation ({model})",
        "",
        "| Figure | Value |",
        "| --- | --- |",
        f"| Unanswerable questions refused | {report.refused}/{len(report.unanswerable)} |",
        f"| False refusals of answerable questions | "
        f"{report.false_refusals}/{len(report.answerable)} |",
        f"| Declined / failed (n={n}) | {report.count('declined')} / {report.count('failed')} |",
        f"| Returned markers naming no cited chunk | "
        f"{report.unresolved_markers} of {report.markers_checked} |",
        f"| Raw invalid-citation rate | {rate_text} |",
        f"| Tokens | {report.input_tokens:,} input, {report.output_tokens:,} output |",
        "",
        "Latency, ms, P50 / P95: "
        + "; ".join(f"{stage.removesuffix('_ms')} {_ms(report.latency(stage))}" for stage in STAGES)
        + ".",
        "",
        "Outcomes: " + outcomes + ".",
        _listing(
            "Unanswerable, not refused",
            [r for r in report.unanswerable if r.outcome != "insufficient_evidence"],
        ),
        _listing(
            "Answerable, refused",
            [r for r in report.answerable if r.outcome == "insufficient_evidence"],
        ),
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


def _listing(label: str, results: Sequence[Any]) -> str:
    names = ", ".join(f"`{result.question_id}` ({result.outcome})" for result in results)
    return f"{label}: {names or 'none'}."


def _ms(latency: Mapping[str, float | int | None]) -> str:
    if latency["n"] == 0:
        return "did not run"
    return f"{latency['p50']} / {latency['p95']} (n={latency['n']})"


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
