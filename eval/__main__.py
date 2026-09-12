"""Run the evaluation: `python -m eval`, or `make eval`.

Verifies the corpus, loads the golden set, ingests the corpus into the eval collection,
checks every quote against the parsed text, measures retrieval, and writes the results.
Exits non-zero only when the harness cannot run: a missed target is a result to record,
not a failure.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.db.session import build_engine, build_session_factory, database_ok
from app.services.embeddings import FastEmbedder
from eval.corpus import CorpusError, load_corpus
from eval.golden import GoldenSetError, check_evidence, load_golden
from eval.ingest import ingest_corpus
from eval.results import (
    RESULTS_DIR,
    render_summary,
    retrieval_record,
    run_metadata,
    write_results,
)
from eval.retrieval import run_retrieval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval", description="Evaluate retrieval against the golden set."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=RESULTS_DIR,
        help=f"where to write the run's JSON record (default: {RESULTS_DIR})",
    )
    args = parser.parse_args(argv)

    started = datetime.now(UTC)
    settings = get_settings()
    engine = build_engine(settings)
    if not database_ok(engine):
        print(
            "eval: database unavailable; start it with `docker compose up -d postgres` "
            "and apply migrations with `uv run alembic upgrade head`",
            file=sys.stderr,
        )
        return 2
    sessions = build_session_factory(engine)

    try:
        corpus = load_corpus()
        golden = load_golden([document.filename for document in corpus.documents])
        embedder = FastEmbedder(settings.embedding_cache_dir)
        ingested = ingest_corpus(sessions, embedder, corpus.documents)
        check_evidence(golden, ingested.pages, embedder.tokenizer)
    except (CorpusError, GoldenSetError) as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 1

    report = run_retrieval(sessions, embedder.embed_query, ingested.collection_id, golden)

    with sessions() as session:
        metadata = run_metadata(session, corpus, golden, started)
    record = {
        "metadata": metadata,
        "chunk_counts": ingested.chunk_counts,
        "retrieval": retrieval_record(report),
    }
    path = write_results(record, args.results_dir, started)

    print(render_summary(report, metadata, ingested.chunk_counts))
    print(f"\nResults written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
