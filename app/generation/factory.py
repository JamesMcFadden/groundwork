"""Choosing the generator a deployment answers with."""

import anthropic

from app.config import Settings
from app.generation.claude import ClaudeGenerator
from app.generation.generator import Generator
from app.generation.stub import StubGenerator

# One retry rides out a momentary overload; more would hold a request open for minutes.
MAX_RETRIES = 1


class GeneratorConfigError(RuntimeError):
    """The configured generator cannot run as configured."""


def build_generator(settings: Settings) -> Generator:
    """Build the generator `GENERATOR` names.

    Selecting the real model without a key fails here, when the API starts, rather than
    on every question. The stub is never a fallback: it answers only when named.
    """
    if settings.generator == "stub":
        return StubGenerator()
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value():
        raise GeneratorConfigError(
            "GENERATOR=anthropic needs ANTHROPIC_API_KEY; set it, or set GENERATOR=stub "
            "to answer from retrieved text without a model"
        )
    client = anthropic.Anthropic(
        api_key=key.get_secret_value(),
        timeout=settings.generation_timeout_seconds,
        max_retries=MAX_RETRIES,
    )
    return ClaudeGenerator(client)
