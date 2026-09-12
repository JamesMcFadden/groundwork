"""The pre-registered comparison: does hybrid search retrieve better than dense search here?

Both strategies search the same ingested collection for the same questions, embedded the
way the service embeds them. The rule was fixed in the roadmap before any hybrid result
existed, and is the bar the query prefix faced: make hybrid search the default only if,
over the answerable questions, it fixes at least two more Recall@5 hits than it breaks and
MRR@10 does not fall.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from eval.golden import GoldenSet
from eval.retrieval import QueryEmbedder, RetrievalReport, flipped_at_5, run_retrieval

# The pre-registered rule: net Recall@5 hits hybrid search must gain before it is adopted.
MIN_NET_FIXED = 2


@dataclass(frozen=True, slots=True)
class RetrieverComparison:
    """The same questions retrieved by dense and by hybrid search, with the verdict."""

    dense: RetrievalReport
    hybrid: RetrievalReport

    @property
    def fixed(self) -> list[str]:
        """Questions with a hit at 5 only under hybrid search."""
        return flipped_at_5(self.dense, self.hybrid, gained=True)

    @property
    def broken(self) -> list[str]:
        """Questions with a hit at 5 only under dense search."""
        return flipped_at_5(self.dense, self.hybrid, gained=False)

    @property
    def net_fixed(self) -> int:
        return len(self.fixed) - len(self.broken)

    @property
    def adopt(self) -> bool:
        """Whether the rule makes hybrid search the default."""
        return self.net_fixed >= MIN_NET_FIXED and self.hybrid.mrr_at_10 >= self.dense.mrr_at_10


def compare_retrievers(
    sessions: sessionmaker[Session],
    embed_query: QueryEmbedder,
    collection_id: uuid.UUID,
    golden: GoldenSet,
) -> RetrieverComparison:
    """Score dense and hybrid search over the same collection and questions."""
    return RetrieverComparison(
        dense=run_retrieval(sessions, embed_query, collection_id, golden, "dense"),
        hybrid=run_retrieval(sessions, embed_query, collection_id, golden, "hybrid"),
    )
