from __future__ import annotations

from math import sqrt

import numpy as np
import pytest
from face_spike.domain import DatasetItem, FaceExpected, LoadedImage
from face_spike.pipeline import FaceEmbedding, ImageAnalysis
from face_spike.retrieval import evaluate_retrieval, rank_candidates


def analysis(filename: str, group: str | None, vector: tuple[float, float]) -> ImageAnalysis:
    pixels = np.zeros((1, 1, 3), dtype=np.uint8)
    image = LoadedImage(
        item=DatasetItem(filename, group, FaceExpected.YES),
        rgb=pixels,
        bgr=pixels,
        width=1,
        height=1,
    )
    return ImageAnalysis(
        image=image,
        detections=(),
        primary_detection=None,
        embedding=FaceEmbedding(np.asarray(vector, dtype=np.float32)),
        status="ok",
    )


def test_rank_candidates_excludes_self_orders_by_distance_and_uses_filename_tie_break() -> None:
    query = analysis("query.jpg", "group-a", (1.0, 0.0))
    results = rank_candidates(
        query,
        (
            analysis("query.jpg", "group-a", (1.0, 0.0)),
            analysis("z-last.jpg", "group-b", (0.0, 1.0)),
            analysis("a-first.jpg", None, (0.0, 1.0)),
            analysis("furthest.jpg", "group-a", (-1.0, 0.0)),
        ),
        top_k=3,
    )

    assert results.query is query
    assert [match.filename for match in results.matches] == [
        "a-first.jpg",
        "z-last.jpg",
        "furthest.jpg",
    ]
    assert [match.distance for match in results.matches] == pytest.approx([1.0, 1.0, 2.0])
    assert [match.same_group for match in results.matches] == [False, False, True]


def test_rank_candidates_uses_exact_cosine_distance_and_truncates_to_top_k() -> None:
    query = analysis("query.jpg", "group-a", (3.0 / 5.0, 4.0 / 5.0))
    results = rank_candidates(
        query,
        (
            analysis("nearest.jpg", "group-b", (0.0, 1.0)),
            analysis("second.jpg", "group-b", (1.0, 0.0)),
        ),
        top_k=1,
    )

    assert [match.filename for match in results.matches] == ["nearest.jpg"]
    assert results.matches[0].distance == pytest.approx(0.2)


def test_rank_candidates_rejects_negative_top_k() -> None:
    query = analysis("query.jpg", "group-a", (1.0, 0.0))

    with pytest.raises(ValueError, match="top_k must be non-negative"):
        rank_candidates(query, (), top_k=-1)


def test_evaluate_retrieval_counts_only_groups_with_another_successful_embedding() -> None:
    analyses = (
        analysis("a-query.jpg", "group-a", (1.0, 0.0)),
        analysis("a-match.jpg", "group-a", (0.8, 0.6)),
        analysis("ungrouped.jpg", None, (0.9, sqrt(0.19))),
        analysis("single.jpg", "group-single", (0.0, 1.0)),
    )

    metrics = evaluate_retrieval(analyses, top_k=10)

    assert len(metrics.queries) == 4
    assert metrics.evaluable_queries == 2
    assert metrics.recall_at_1 == 0.0
    assert metrics.recall_at_5 == 1.0
    assert metrics.recall_at_10 == 1.0
    query_result = next(result for result in metrics.queries if result.filename == "a-query.jpg")
    assert query_result.matches[0].filename == "ungrouped.jpg"
    assert query_result.matches[0].same_group is False


def test_evaluate_retrieval_reports_recall_at_each_requested_cutoff() -> None:
    metrics = evaluate_retrieval(
        (
            analysis("a-1.jpg", "group-a", (1.0, 0.0)),
            analysis("a-2.jpg", "group-a", (0.8, 0.6)),
            analysis("b-1.jpg", "group-b", (0.0, 1.0)),
            analysis("b-2.jpg", "group-b", (0.6, 0.8)),
        ),
        top_k=10,
    )

    assert metrics.evaluable_queries == 4
    assert metrics.recall_at_1 == 0.5
    assert metrics.recall_at_5 == 1.0
    assert metrics.recall_at_10 == 1.0


def test_evaluate_retrieval_returns_null_recalls_without_evaluable_queries() -> None:
    metrics = evaluate_retrieval(
        (
            analysis("ungrouped.jpg", None, (1.0, 0.0)),
            analysis("single.jpg", "group-single", (0.0, 1.0)),
        ),
        top_k=10,
    )

    assert metrics.evaluable_queries == 0
    assert metrics.recall_at_1 is None
    assert metrics.recall_at_5 is None
    assert metrics.recall_at_10 is None


def test_evaluate_retrieval_is_stable_across_repeated_calls() -> None:
    analyses = (
        analysis("b.jpg", "group-a", (0.0, 1.0)),
        analysis("a.jpg", "group-a", (1.0, 0.0)),
        analysis("negative.jpg", None, (sqrt(0.5), sqrt(0.5))),
    )

    assert evaluate_retrieval(analyses, top_k=2) == evaluate_retrieval(analyses, top_k=2)
