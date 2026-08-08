"""Fail-closed final approval for a reviewed local quality comparison."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .benchmark import FinalBenchmark
from .quality_comparison import QualityComparison, QualityRun, compare_quality_runs
from .quality_comparison_artifacts import (
    ALLOWED_LABELS,
    quality_comparison_sha256,
)
from .smoke_search import SearchComparison


@dataclass(frozen=True)
class QualityApproval:
    review_bundle_sha256: str
    comparison_sha256: str
    configuration_sha256: str
    generation_sha256: str
    reviewer: str
    reviewed_at: str
    clear_loss_count: int
    uncertain_rejection_count: int
    blurred_rejection_count: int
    unusably_small_rejection_count: int
    relevant_result_loss_count: int
    unresolved_count: int
    newly_rejected_count: int
    approved: bool = True


def finalize_quality_review(
    comparison: QualityComparison,
    labels: Mapping[str, str],
    search: SearchComparison,
    recomputed_search: SearchComparison,
    baseline_run: QualityRun,
    candidate_run: QualityRun,
    benchmark: FinalBenchmark,
    review_bundle_sha256: str,
    reviewer: str,
    reviewed_at: datetime,
) -> QualityApproval:
    """Authorize only complete evidence with no clear, uncertain, search, or corpus loss."""
    if (
        not isinstance(comparison, QualityComparison)
        or not isinstance(search, SearchComparison)
        or not isinstance(recomputed_search, SearchComparison)
        or not isinstance(baseline_run, QualityRun)
        or not isinstance(candidate_run, QualityRun)
        or not isinstance(benchmark, FinalBenchmark)
    ):
        raise TypeError("comparison evidence is invalid")
    from photo_worker.face_quality import FaceQualityThresholds

    configuration = comparison.quality_configuration
    if dict(candidate_run.quality_configuration) != dict(configuration):
        raise ValueError("candidate source quality configuration differs")
    reconstructed = compare_quality_runs(
        baseline_run,
        candidate_run,
        thresholds=FaceQualityThresholds(
            algorithm_version=configuration["algorithm_version"],
            crop_size=configuration["crop_size"],
            minimum_face_px=configuration["minimum_face_px"],
            severe_blur_threshold=configuration["severe_blur_threshold"],
            borderline_blur_threshold=configuration["borderline_blur_threshold"],
            minimum_relative_area=configuration["minimum_relative_area"],
            minimum_confidence=configuration["minimum_confidence"],
        ),
        minimum_iou=comparison.minimum_iou,
        threshold_band_fraction=comparison.threshold_band_fraction,
        samples_per_metric=comparison.samples_per_metric,
    )
    if reconstructed != comparison:
        raise ValueError("quality comparison source evidence differs")
    if search.quality_comparison_sha256 != quality_comparison_sha256(comparison):
        raise ValueError("search comparison source differs")
    if search != recomputed_search:
        raise ValueError("search comparison differs from recomputed index evidence")
    _validate_search_benchmark(search, benchmark, comparison)
    if len(review_bundle_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in review_bundle_sha256
    ):
        raise ValueError("review bundle source is invalid")
    expected_ids = {item.candidate_face_id for item in comparison.new_rejections}
    if set(labels) != expected_ids or any(label not in ALLOWED_LABELS for label in labels.values()):
        raise ValueError("quality labels are incomplete or invalid")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 100:
        raise ValueError("reviewer identity is invalid")
    if "\r" in reviewer or "\n" in reviewer:
        raise ValueError("reviewer identity is invalid")
    if reviewed_at.tzinfo is None or reviewed_at.utcoffset() != UTC.utcoffset(reviewed_at):
        raise ValueError("review time must be UTC")
    counts = Counter(labels.values())
    relevant_losses = sum(len(result.lost_confirmed_relevant) for result in search.query_results)
    if counts["clear"] or counts["uncertain"] or relevant_losses or comparison.unresolved_photos:
        raise ValueError("quality comparison cannot be approved")
    configuration_sha256 = hashlib.sha256(
        json.dumps(
            dict(comparison.quality_configuration),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return QualityApproval(
        review_bundle_sha256=review_bundle_sha256,
        comparison_sha256=quality_comparison_sha256(comparison),
        configuration_sha256=configuration_sha256,
        generation_sha256=comparison.candidate_generation_sha256,
        reviewer=reviewer.strip(),
        reviewed_at=reviewed_at.isoformat().replace("+00:00", "Z"),
        clear_loss_count=0,
        uncertain_rejection_count=0,
        blurred_rejection_count=counts["blurred"],
        unusably_small_rejection_count=counts["unusably_small"],
        relevant_result_loss_count=0,
        unresolved_count=0,
        newly_rejected_count=len(comparison.new_rejections),
    )


def _validate_search_benchmark(
    search: SearchComparison,
    benchmark: FinalBenchmark,
    comparison: QualityComparison,
) -> None:
    from .benchmark_artifacts import final_benchmark_sha256
    from .smoke_search import SearchComparisonPhoto

    if search.benchmark_sha256 != final_benchmark_sha256(benchmark):
        raise ValueError("search benchmark source differs")
    results = {item.query_id: item for item in search.query_results}
    expected_ids = tuple(query.query_id for query in benchmark.queries)
    if tuple(results) != expected_ids:
        raise ValueError("search benchmark query set differs")
    face_by_id = benchmark.face_by_id
    relevant_by_query = {
        query.query_id: tuple(
            sorted(
                {
                    face_by_id[item.candidate_face_id].filename
                    for item in benchmark.annotations
                    if item.query_id == query.query_id and item.label == "relevant"
                }
            )
        )
        for query in benchmark.queries
    }
    run_sha256 = hashlib.sha256(
        json.dumps(
            {
                "faces_sha256": benchmark.source.faces_sha256,
                "manifest_sha256": benchmark.source.run_manifest_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    rejected_baseline_ids = {item.baseline_face_id for item in comparison.new_rejections}
    for query in benchmark.queries:
        result = results[query.query_id]
        if (
            result.confirmed_relevant != relevant_by_query[query.query_id]
            or result.source_run_sha256 != run_sha256
            or result.query_crop_sha256 != face_by_id[query.query_face_id].crop_sha256
        ):
            raise ValueError("search query evidence differs from benchmark")
        expected_supports = tuple(
            SearchComparisonPhoto(item.face_id, item.filename)
            for item in result.baseline_results
            if item.filename in result.lost_confirmed_relevant
            and item.face_id in rejected_baseline_ids
        )
        if result.quality_rejected_supports != expected_supports:
            raise ValueError("quality rejection causation differs")


def write_quality_approval(output: Path, approval: QualityApproval) -> None:
    """Atomically publish one bounded aggregate approval directory."""
    if not isinstance(approval, QualityApproval) or approval.approved is not True:
        raise TypeError("approved aggregate is required")
    if os.path.lexists(output):
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.approval.", dir=output.parent))
    try:
        payload = {
            "schema_version": 1,
            "artifact_type": "quality-approval",
            "review_bundle_sha256": approval.review_bundle_sha256,
            "comparison_sha256": approval.comparison_sha256,
            "configuration_sha256": approval.configuration_sha256,
            "generation_sha256": approval.generation_sha256,
            "reviewer": approval.reviewer,
            "reviewed_at": approval.reviewed_at,
            "approved": approval.approved,
            "clear_loss_count": approval.clear_loss_count,
            "uncertain_rejection_count": approval.uncertain_rejection_count,
            "blurred_rejection_count": approval.blurred_rejection_count,
            "unusably_small_rejection_count": approval.unusably_small_rejection_count,
            "relevant_result_loss_count": approval.relevant_result_loss_count,
            "unresolved_count": approval.unresolved_count,
            "newly_rejected_count": approval.newly_rejected_count,
        }
        serialized = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            + "\n"
        )
        if len(serialized.encode("utf-8")) > 4096:
            raise ValueError("approval aggregate is too large")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".approval.", suffix=".tmp", dir=staging
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, staging / "approval.json")
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        if os.path.lexists(output):
            raise FileExistsError(output)
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
