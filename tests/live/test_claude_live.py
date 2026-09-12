"""Tests against the real Claude API. They cost money, so they run only when selected:

    ANTHROPIC_API_KEY=... uv run pytest -m live tests/live

CI runs them on pushes to main and on manual dispatch, with a key from a Claude Console
workspace that has a monthly spend limit. Each run makes two small requests.
"""

import os

import anthropic
import pytest

from app.generation.citations import check_citations
from app.generation.claude import ClaudeGenerator
from app.generation.context import Passage
from app.generation.factory import MAX_RETRIES

pytestmark = pytest.mark.live

# The service's default, from GENERATION_TIMEOUT_SECONDS.
TIMEOUT_SECONDS = 60.0

PASSAGES = [
    Passage(
        1,
        "An ingestion worker refreshes its job's heartbeat before embedding each batch.",
        "operations.pdf",
        4,
        4,
    ),
    Passage(
        2,
        "A job whose heartbeat is more than five minutes old is reclaimed by the next "
        "worker that polls for work.",
        "operations.pdf",
        5,
        5,
    ),
]


@pytest.fixture(scope="module")
def generator() -> ClaudeGenerator:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("set ANTHROPIC_API_KEY to run the live Claude tests")
    return ClaudeGenerator(anthropic.Anthropic(timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES))


def test_an_answerable_question_is_answered_with_valid_citations(
    generator: ClaudeGenerator,
) -> None:
    """Also confirms the API accepts low effort and a JSON schema format together."""
    question = "How stale can a job's heartbeat get before another worker reclaims it?"

    generation = generator.generate(question, PASSAGES)

    checked = check_citations(generation, PASSAGES)
    assert generation.insufficient_evidence is False
    assert checked.statements, "no statement survived citation checks"
    assert 2 in checked.cited
    assert generation.input_tokens and generation.output_tokens


def test_a_question_the_passages_cannot_answer_is_insufficient_evidence(
    generator: ClaudeGenerator,
) -> None:
    """Judged as the service judges it: flagged by the model, or left with no citation."""
    generation = generator.generate("Which port does the metrics exporter listen on?", PASSAGES)

    checked = check_citations(generation, PASSAGES)
    assert generation.insufficient_evidence or not checked.statements
