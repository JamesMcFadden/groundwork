"""A deterministic stand-in for the model."""

from collections.abc import Sequence

from app.generation.context import Passage
from app.generation.generator import Generation, Statement

# Long enough to read as an answer, short enough to keep responses small under load.
OPENING_WORDS = 30


class StubGenerator:
    """Answers with the opening words of the top passage, citing it.

    No network call and no randomness: the same passages always get the same answer.
    Load tests use it so they measure this service rather than a model provider, and CI
    uses it so the suite needs no API key and costs nothing.
    """

    def generate(self, question: str, passages: Sequence[Passage]) -> Generation:
        if not passages:
            return Generation(statements=(), insufficient_evidence=True)
        top = passages[0]
        opening = " ".join(top.text.split()[:OPENING_WORDS])
        return Generation(
            statements=(Statement(text=opening, citations=(top.number,)),),
            insufficient_evidence=False,
        )
