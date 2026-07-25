from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from face_spike.pipeline import ImageAnalysis


@dataclass(frozen=True)
class RetrievalMatch:
    candidate: ImageAnalysis
    distance: float
    same_group: bool

    @property
    def filename(self) -> str:
        return self.candidate.image.item.filename

    @property
    def participant_group(self) -> str | None:
        return self.candidate.image.item.participant_group


@dataclass(frozen=True)
class QueryRetrieval:
    query: ImageAnalysis
    matches: tuple[RetrievalMatch, ...]

    @property
    def filename(self) -> str:
        return self.query.image.item.filename

    @property
    def participant_group(self) -> str | None:
        return self.query.image.item.participant_group


@dataclass(frozen=True)
class RetrievalMetrics:
    queries: tuple[QueryRetrieval, ...]
    evaluable_queries: int
    recall_at_1: float | None
    recall_at_5: float | None
    recall_at_10: float | None


def rank_candidates(
    query: ImageAnalysis,
    candidates: Sequence[ImageAnalysis],
    top_k: int,
) -> QueryRetrieval:
    if top_k < 0:
        raise ValueError("top_k must be non-negative")
    query_embedding = _embedding_for(query)
    query_filename = query.image.item.filename
    query_group = query.image.item.participant_group
    matches = tuple(
        sorted(
            (
                RetrievalMatch(
                    candidate=candidate,
                    distance=_cosine_distance(query_embedding, candidate_embedding),
                    same_group=(
                        query_group is not None
                        and query_group == candidate.image.item.participant_group
                    ),
                )
                for candidate in candidates
                if candidate.image.item.filename != query_filename
                and (candidate_embedding := _successful_embedding(candidate)) is not None
            ),
            key=lambda match: (match.distance, match.filename),
        )[:top_k]
    )
    return QueryRetrieval(query=query, matches=matches)


def evaluate_retrieval(
    analyses: Sequence[ImageAnalysis],
    top_k: int,
) -> RetrievalMetrics:
    if top_k < 0:
        raise ValueError("top_k must be non-negative")
    successful = tuple(
        sorted(
            (analysis for analysis in analyses if _successful_embedding(analysis) is not None),
            key=lambda analysis: analysis.image.item.filename,
        )
    )
    group_counts = Counter(
        analysis.image.item.participant_group
        for analysis in successful
        if analysis.image.item.participant_group is not None
    )
    full_rank_limit = max(top_k, 10)
    full_queries = tuple(
        rank_candidates(query, successful, full_rank_limit) for query in successful
    )
    queries = tuple(
        QueryRetrieval(query=result.query, matches=result.matches[:top_k])
        for result in full_queries
    )
    evaluable = tuple(
        result
        for result in full_queries
        if (group := result.participant_group) is not None and group_counts[group] > 1
    )
    return RetrievalMetrics(
        queries=queries,
        evaluable_queries=len(evaluable),
        recall_at_1=_recall_at(evaluable, 1),
        recall_at_5=_recall_at(evaluable, 5),
        recall_at_10=_recall_at(evaluable, 10),
    )


def _successful_embedding(analysis: ImageAnalysis) -> np.ndarray | None:
    if analysis.status != "ok" or analysis.embedding is None:
        return None
    return analysis.embedding.vector


def _embedding_for(analysis: ImageAnalysis) -> np.ndarray:
    embedding = _successful_embedding(analysis)
    if embedding is None:
        raise ValueError("query must have a successful embedding")
    return embedding


def _cosine_distance(query_embedding: np.ndarray, candidate_embedding: np.ndarray) -> float:
    distance = 1.0 - float(
        np.dot(query_embedding.astype(np.float64), candidate_embedding.astype(np.float64))
    )
    return float(np.clip(distance, 0.0, 2.0))


def _recall_at(queries: Sequence[QueryRetrieval], cutoff: int) -> float | None:
    if not queries:
        return None
    hits = sum(any(match.same_group for match in query.matches[:cutoff]) for query in queries)
    return hits / len(queries)
