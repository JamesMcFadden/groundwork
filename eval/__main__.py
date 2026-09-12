"""Run the evaluation: `python -m eval`, or `make eval` and `make eval-live`.

Verifies the corpus, loads the golden set, ingests the corpus into the eval collection,
checks every quote against the parsed text, measures retrieval, puts every question
through the answering path, and writes the results. Exits non-zero only when the harness
cannot run: a missed target is a result to record, not a failure.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings, get_settings
from app.db.session import build_engine, build_session_factory, database_ok
from app.generation.factory import GeneratorConfigError, build_generator
from app.generation.generator import Generator
from app.generation.stub import StubGenerator
from app.services.embeddings import FastEmbedder
from eval.answering import run_answering
from eval.corpus import CorpusError, load_corpus
from eval.golden import GoldenSetError, check_evidence, load_golden
from eval.ingest import ingest_corpus
from eval.results import (
    RESULTS_DIR,
    answering_record,
    render_answering,
    render_summary,
    retrieval_record,
    run_metadata,
    write_results,
)
from eval.retrieval import run_retrieval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval", description="Evaluate the service against the golden set."
    )
    parser.add_argument(
        "--answers",
        choices=["stub", "claude"],
        default="stub",
        help="who answers: the stub, free and not measured (default), or Claude on "
        "ANTHROPIC_API_KEY, which costs money and is measured",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=RESULTS_DIR,
        help=f"where to write the run's JSON record (default: {RESULTS_DIR})",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        help="also append the Markdown summary to this file, such as $GITHUB_STEP_SUMMARY",
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
        # First, so a missing API key stops the run before anything is ingested.
        generator = _generator(args.answers, settings)
        corpus = load_corpus()
        golden = load_golden([document.filename for document in corpus.documents])
        embedder = FastEmbedder(settings.embedding_cache_dir)
        ingested = ingest_corpus(sessions, embedder, corpus.documents)
        check_evidence(golden, ingested.pages, embedder.tokenizer)
    except (CorpusError, GoldenSetError, GeneratorConfigError) as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 1

    retrieval = run_retrieval(sessions, embedder.embed_query, ingested.collection_id, golden)
    answering = run_answering(sessions, embedder, generator, ingested, golden)
    measured = args.answers == "claude"

    with sessions() as session:
        metadata = run_metadata(session, corpus, golden, started, args.answers)
    record = {
        "metadata": metadata,
        "chunk_counts": ingested.chunk_counts,
        "retrieval": retrieval_record(retrieval),
        "answering": answering_record(answering, measured),
    }
    path = write_results(record, args.results_dir, started)

    summary = "\n\n".join(
        [
            render_summary(retrieval, metadata, ingested.chunk_counts),
            render_answering(answering, measured, metadata["generation_model"]),
        ]
    )
    print(summary)
    if args.summary is not None:
        with args.summary.open("a") as file:
            file.write(summary + "\n")
    print(f"\nResults written to {path}")
    return 0


def _generator(answers: str, settings: Settings) -> Generator:
    if answers == "stub":
        return StubGenerator()
    # The service's own factory, so a live run has the deployment's timeout and retries.
    try:
        return build_generator(settings.model_copy(update={"generator": "anthropic"}))
    except GeneratorConfigError:
        # The factory's advice is about GENERATOR, which the harness does not read.
        raise GeneratorConfigError(
            "--answers claude needs ANTHROPIC_API_KEY in the environment or .env; "
            "`make eval` runs without one, leaving answers unmeasured"
        ) from None


if __name__ == "__main__":
    sys.exit(main())
