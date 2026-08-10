from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_benchmark import _valid_annotations
from test_benchmark_artifacts import _proposal
from test_quality_comparison import _face, _photo, _run, _thresholds
from test_quality_comparison_artifacts import _comparison, _comparison_evidence


def _benchmark():
    from face_spike.benchmark import finalize_benchmark

    proposal = _proposal()
    return finalize_benchmark(proposal, _valid_annotations(proposal))


def _search(comparison, benchmark, *, lost_query_id: str | None = None):
    from face_spike.benchmark_artifacts import final_benchmark_sha256
    from face_spike.quality_comparison_artifacts import quality_comparison_sha256
    from face_spike.smoke_search import (
        SearchComparison,
        SearchComparisonPhoto,
        SearchComparisonQueryResult,
    )

    face_by_id = benchmark.face_by_id
    run_sha256 = hashlib.sha256(
        json.dumps(
            {
                "faces_sha256": benchmark.source.faces_sha256,
                "manifest_sha256": benchmark.source.run_manifest_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    results = []
    for query in benchmark.queries:
        confirmed = tuple(
            sorted(
                {
                    face_by_id[item.candidate_face_id].filename
                    for item in benchmark.annotations
                    if item.query_id == query.query_id and item.label == "relevant"
                }
            )
        )
        baseline = tuple(
            SearchComparisonPhoto(f"support-{index}", filename)
            for index, filename in enumerate(confirmed)
        )
        lost = (confirmed[-1],) if query.query_id == lost_query_id else ()
        candidate = tuple(item for item in baseline if item.filename not in lost)
        results.append(
            SearchComparisonQueryResult(
                query_id=query.query_id,
                source_run_sha256=run_sha256,
                query_crop_sha256=face_by_id[query.query_face_id].crop_sha256,
                baseline_results=baseline,
                candidate_results=candidate,
                confirmed_relevant=confirmed,
                lost_confirmed_relevant=lost,
                quality_rejected_supports=(),
            )
        )

    return SearchComparison(
        quality_comparison_sha256(comparison),
        final_benchmark_sha256(benchmark),
        "b" * 64,
        "c" * 64,
        direct_threshold=0.363,
        query_results=tuple(results),
    )


def test_finalization_fails_closed_on_losses_uncertainty_or_unresolved() -> None:
    from face_spike.quality_comparison_report import finalize_quality_review

    comparison = _comparison()
    _comparison_value, baseline_run, candidate_run = _comparison_evidence()
    benchmark = _benchmark()
    reviewed_at = datetime(2026, 8, 8, tzinfo=UTC)
    for labels, search in (
        ({"candidate": "clear"}, _search(comparison, benchmark)),
        ({"candidate": "uncertain"}, _search(comparison, benchmark)),
        (
            {"candidate": "blurred"},
            _search(comparison, benchmark, lost_query_id=benchmark.queries[0].query_id),
        ),
    ):
        with pytest.raises(ValueError):
            finalize_quality_review(
                comparison,
                labels,
                search,
                search,
                baseline_run,
                candidate_run,
                benchmark,
                "f" * 64,
                "reviewer",
                reviewed_at,
            )

    from face_spike.quality_comparison import compare_quality_runs

    unresolved = compare_quality_runs(
        _run(_photo("photo.jpg", _face("baseline", "photo.jpg", (0, 0, 20, 20)))),
        _run(_photo("photo.jpg")),
        thresholds=_thresholds(),
    )
    with pytest.raises(ValueError):
        unresolved_search = _search(unresolved, benchmark)
        finalize_quality_review(
            unresolved,
            {"candidate": "blurred"},
            unresolved_search,
            unresolved_search,
            baseline_run,
            candidate_run,
            benchmark,
            "f" * 64,
            "reviewer",
            reviewed_at,
        )
    with pytest.raises(ValueError):
        source_search = _search(comparison, benchmark)
        finalize_quality_review(
            comparison,
            {"candidate": "blurred"},
            replace(source_search, quality_comparison_sha256="f" * 64),
            source_search,
            baseline_run,
            candidate_run,
            benchmark,
            "f" * 64,
            "reviewer",
            reviewed_at,
        )


def test_approved_output_is_bounded_aggregate_immutable_and_biometric_free(
    tmp_path: Path,
) -> None:
    from face_spike.quality_comparison_report import (
        finalize_quality_review,
        write_quality_approval,
    )

    comparison, baseline_run, candidate_run = _comparison_evidence()
    benchmark = _benchmark()
    search = _search(comparison, benchmark)
    approval = finalize_quality_review(
        comparison,
        {"candidate": "blurred"},
        search,
        search,
        baseline_run,
        candidate_run,
        benchmark,
        "f" * 64,
        "reviewer",
        datetime(2026, 8, 8, tzinfo=UTC),
    )
    output = tmp_path / "approval"
    write_quality_approval(output, approval)

    payload = json.loads((output / "approval.json").read_text(encoding="utf-8"))
    assert payload["approved"] is True
    assert payload["clear_loss_count"] == 0
    assert payload["uncertain_rejection_count"] == 0
    assert payload["relevant_result_loss_count"] == 0
    assert payload["unresolved_count"] == 0
    serialized = json.dumps(payload).lower()
    assert "candidate" not in serialized
    assert "embedding" not in serialized
    assert "vector" not in serialized
    assert len(serialized) < 4096
    with pytest.raises(FileExistsError):
        write_quality_approval(output, approval)


def test_finalization_rejects_coordinated_source_face_omission() -> None:
    from face_spike.quality_comparison import compare_quality_runs
    from face_spike.quality_comparison_report import finalize_quality_review

    _comparison_value, baseline_run, candidate_run = _comparison_evidence()
    omitted_baseline = replace(
        baseline_run,
        photos=tuple(replace(photo, faces=()) for photo in baseline_run.photos),
    )
    omitted_candidate = replace(
        candidate_run,
        photos=tuple(replace(photo, faces=()) for photo in candidate_run.photos),
    )
    omitted = compare_quality_runs(omitted_baseline, omitted_candidate, thresholds=_thresholds())
    benchmark = _benchmark()

    with pytest.raises(ValueError, match="source evidence differs"):
        finalize_quality_review(
            omitted,
            {},
            _search(omitted, benchmark),
            _search(omitted, benchmark),
            baseline_run,
            candidate_run,
            benchmark,
            "f" * 64,
            "reviewer",
            datetime(2026, 8, 8, tzinfo=UTC),
        )


def test_finalization_rejects_omitted_benchmark_query() -> None:
    from face_spike.quality_comparison_report import finalize_quality_review

    comparison, baseline_run, candidate_run = _comparison_evidence()
    benchmark = _benchmark()
    complete = _search(comparison, benchmark)
    truncated = replace(complete, query_results=complete.query_results[1:])

    with pytest.raises(ValueError, match="recomputed index evidence"):
        finalize_quality_review(
            comparison,
            {"candidate": "blurred"},
            truncated,
            complete,
            baseline_run,
            candidate_run,
            benchmark,
            "f" * 64,
            "reviewer",
            datetime(2026, 8, 8, tzinfo=UTC),
        )


def test_finalization_rejects_query_crop_digest_not_frozen_by_benchmark() -> None:
    from face_spike.quality_comparison_report import finalize_quality_review

    comparison, baseline_run, candidate_run = _comparison_evidence()
    benchmark = _benchmark()
    search = _search(comparison, benchmark)
    changed_first = replace(search.query_results[0], query_crop_sha256="0" * 64)
    changed = replace(search, query_results=(changed_first, *search.query_results[1:]))

    with pytest.raises(ValueError, match="recomputed index evidence"):
        finalize_quality_review(
            comparison,
            {"candidate": "blurred"},
            changed,
            search,
            baseline_run,
            candidate_run,
            benchmark,
            "f" * 64,
            "reviewer",
            datetime(2026, 8, 8, tzinfo=UTC),
        )


def test_finalization_rejects_configuration_not_owned_by_candidate_run() -> None:
    from face_spike.quality_comparison_report import finalize_quality_review

    comparison, baseline_run, candidate_run = _comparison_evidence()
    altered = replace(
        comparison,
        quality_configuration={
            **comparison.quality_configuration,
            "minimum_confidence": 0.83,
        },
    )
    benchmark = _benchmark()
    search = _search(altered, benchmark)

    with pytest.raises(ValueError, match="source quality configuration differs"):
        finalize_quality_review(
            altered,
            {"candidate": "blurred"},
            search,
            search,
            baseline_run,
            candidate_run,
            benchmark,
            "f" * 64,
            "reviewer",
            datetime(2026, 8, 8, tzinfo=UTC),
        )


def test_finalization_rejects_rehashed_search_result_loss_erasure() -> None:
    from face_spike.quality_comparison_report import finalize_quality_review

    comparison, baseline_run, candidate_run = _comparison_evidence()
    benchmark = _benchmark()
    lost_query_id = benchmark.queries[0].query_id
    recomputed = _search(comparison, benchmark, lost_query_id=lost_query_id)
    loss = recomputed.query_results[0]
    rewritten = replace(
        loss,
        candidate_results=loss.baseline_results,
        lost_confirmed_relevant=(),
        quality_rejected_supports=(),
    )
    tampered = replace(
        recomputed,
        query_results=(rewritten, *recomputed.query_results[1:]),
    )

    with pytest.raises(ValueError, match="recomputed index evidence"):
        finalize_quality_review(
            comparison,
            {"candidate": "blurred"},
            tampered,
            recomputed,
            baseline_run,
            candidate_run,
            benchmark,
            "f" * 64,
            "reviewer",
            datetime(2026, 8, 8, tzinfo=UTC),
        )
