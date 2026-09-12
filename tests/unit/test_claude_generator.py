import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest

from app.generation.claude import MODEL, ClaudeGenerator
from app.generation.context import Passage
from app.generation.generator import GenerationDeclined, GenerationIncomplete, Statement

PASSAGES = [
    Passage(1, "Workers refresh a heartbeat while they hold a job.", "ops.pdf", 4, 4),
    Passage(2, "A stale heartbeat lets another worker reclaim the job.", "ops.pdf", 5, 5),
]
QUESTION = "How is an abandoned job recovered?"


def generator_answering(respond: Callable[[httpx2.Request], httpx2.Response]) -> ClaudeGenerator:
    """A generator whose HTTP requests are answered in memory by `respond`, not the API."""
    client = anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(respond)),
    )
    return ClaudeGenerator(client)


def message(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    """A Messages API response body: a thinking block with its text omitted, then text."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [
            {"type": "thinking", "thinking": "", "signature": "signature"},
            {"type": "text", "text": text},
        ],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 812, "output_tokens": 240},
    }


def replying(body: dict[str, Any]) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda request: httpx2.Response(200, json=body)


NO_ANSWER = json.dumps({"insufficient_evidence": True, "statements": []})


def test_the_request_asks_for_structured_statements_at_low_effort() -> None:
    sent: list[dict[str, Any]] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=message(NO_ANSWER))

    generator_answering(respond).generate(QUESTION, PASSAGES)

    (body,) = sent
    assert body["model"] == "claude-opus-5"
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert set(body["output_config"]["format"]["schema"]["properties"]) == {
        "insufficient_evidence",
        "statements",
    }
    # Thinking is on by default for this model and never disabled; no assistant prefill.
    assert "thinking" not in body
    assert [turn["role"] for turn in body["messages"]] == ["user"]
    prompt = body["messages"][0]["content"]
    assert '<passage number="2" source="ops.pdf" pages="5">' in prompt
    assert QUESTION in prompt


def test_statements_and_their_citations_come_back_as_a_generation() -> None:
    answer = {
        "insufficient_evidence": False,
        "statements": [
            {"text": "A worker holding a job refreshes its heartbeat.", "citations": [1]},
            {"text": "Once it goes stale, another worker reclaims the job.", "citations": [2, 1]},
        ],
    }
    generator = generator_answering(replying(message(json.dumps(answer))))

    generation = generator.generate(QUESTION, PASSAGES)

    assert generation.statements == (
        Statement("A worker holding a job refreshes its heartbeat.", (1,)),
        Statement("Once it goes stale, another worker reclaims the job.", (2, 1)),
    )
    assert (generation.insufficient_evidence, generation.input_tokens) == (False, 812)
    assert generation.output_tokens == 240


def test_the_models_own_insufficient_evidence_judgement_is_kept() -> None:
    generation = generator_answering(replying(message(NO_ANSWER))).generate(QUESTION, PASSAGES)

    assert (generation.statements, generation.insufficient_evidence) == ((), True)


def test_a_refusal_is_declined_before_its_text_is_read() -> None:
    """A refusal's text is not the promised JSON. Read first, it would misreport a
    refusal as a malformed answer, and the question would be recorded as failed."""
    generator = generator_answering(replying(message("I can't help with that.", "refusal")))

    with pytest.raises(GenerationDeclined) as declined:
        generator.generate(QUESTION, PASSAGES)

    assert (declined.value.input_tokens, declined.value.output_tokens) == (812, 240)


def test_an_answer_cut_off_at_the_token_limit_is_incomplete() -> None:
    truncated = message('{"insufficient_evidence": false, "statem', "max_tokens")
    generator = generator_answering(replying(truncated))

    with pytest.raises(GenerationIncomplete, match="max_tokens") as incomplete:
        generator.generate(QUESTION, PASSAGES)

    assert incomplete.value.output_tokens == 240


def test_a_timeout_surfaces_as_the_sdks_timeout_error() -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    with pytest.raises(anthropic.APITimeoutError):
        generator_answering(respond).generate(QUESTION, PASSAGES)
