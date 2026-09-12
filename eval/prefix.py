"""The pre-registered comparison: does bge's query instruction improve retrieval here?

bge-small-en-v1.5 was trained with an instruction for retrieval queries, and fastembed
does not add it. Whether it helps on this corpus is measured rather than assumed, under a
rule fixed in the roadmap before any result existed: adopt the prefix only if, over the
answerable questions, it fixes at least two more Recall@5 hits than it breaks and MRR@10
does not fall.

Both variants embed through the passage embedder, which for this model is exactly what
the service's query embedding does today; a test asserts it. Building them explicitly
keeps the comparison reproducible if the service's own query embedding later changes.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.services.embeddings import Embedder
from eval.golden import GoldenSet
from eval.retrieval import QueryEmbedder, RetrievalReport, flipped_at_5, run_retrieval

QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

# The pre-registered rule: net Recall@5 hits the prefix must gain before it is adopted.
MIN_NET_FIXED = 2

PassageEmbedder = Callable[[Sequence[str]], list[list[float]]]


def query_embedders(
    embed_passages: PassageEmbedder, prefix: str = QUERY_INSTRUCTION
) -> tuple[QueryEmbedder, QueryEmbedder]:
    """Embed a question as it is, and with the instruction in front of it."""

    def without(question: str) -> list[float]:
        return embed_passages([question])[0]

    def with_prefix(question: str) -> list[float]:
        return embed_passages([prefix + question])[0]

    return without, with_prefix


@dataclass(frozen=True, slots=True)
class PrefixComparison:
    """The same questions retrieved both ways, with the pre-registered verdict."""

    without: RetrievalReport
    with_prefix: RetrievalReport

    @property
    def fixed(self) -> list[str]:
        """Questions with a hit at 5 only when the prefix is added."""
        return flipped_at_5(self.without, self.with_prefix, gained=True)

    @property
    def broken(self) -> list[str]:
        """Questions with a hit at 5 only when it is not."""
        return flipped_at_5(self.without, self.with_prefix, gained=False)

    @property
    def net_fixed(self) -> int:
        return len(self.fixed) - len(self.broken)

    @property
    def adopt(self) -> bool:
        return (
            self.net_fixed >= MIN_NET_FIXED and self.with_prefix.mrr_at_10 >= self.without.mrr_at_10
        )


def compare_prefix(
    sessions: sessionmaker[Session],
    embedder: Embedder,
    collection_id: uuid.UUID,
    golden: GoldenSet,
) -> PrefixComparison:
    """Score retrieval over the same collection and questions, without and with the prefix.

    Always dense search, whatever the run's retriever: the rule was pre-registered for it,
    and applied in M3.
    """
    without, with_prefix = query_embedders(embedder.embed_passages)
    return PrefixComparison(
        without=run_retrieval(sessions, without, collection_id, golden, "dense"),
        with_prefix=run_retrieval(sessions, with_prefix, collection_id, golden, "dense"),
    )
