"""Deterministic, vector-free comparison of two face-quality runs."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from .analysis import BoundingBox

if TYPE_CHECKING:
    from photo_worker.face_quality import FaceQualityEvidence, FaceQualityThresholds

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_PHOTO_STATUSES = frozenset({"ok", "no_detection"})
_FACE_STATUSES = frozenset({"accepted", "quality_rejected", "technical_failed"})


@dataclass(frozen=True)
class QualityFace:
    face_id: str
    filename: str
    bounding_box: BoundingBox
    crop_path: str
    status: str
    quality: FaceQualityEvidence | None
    technical_failure: str | None = None

    def __post_init__(self) -> None:
        from photo_worker.face_quality import FaceQualityEvidence

        path = Path(self.crop_path)
        box = self.bounding_box
        valid_box = (
            all(math.isfinite(value) for value in (box.x, box.y, box.width, box.height))
            and box.width > 0
            and box.height > 0
        )
        normal = self.status in {"accepted", "quality_rejected"}
        expected_decision = "accepted" if self.status == "accepted" else "quality_rejected"
        if not (
            self.face_id
            and self.filename
            and self.status in _FACE_STATUSES
            and self.crop_path
            and not path.is_absolute()
            and ".." not in path.parts
            and valid_box
            and (
                normal
                and isinstance(self.quality, FaceQualityEvidence)
                and self.quality.decision == expected_decision
                and self.technical_failure is None
                or self.status == "technical_failed"
                and isinstance(self.quality, FaceQualityEvidence)
                and self.quality.decision == "accepted"
                and isinstance(self.technical_failure, str)
                and bool(self.technical_failure)
            )
        ):
            raise ValueError("invalid quality face")


@dataclass(frozen=True)
class QualityPhoto:
    filename: str
    status: str
    faces: tuple[QualityFace, ...]
    technical_failure: str | None = None

    def __post_init__(self) -> None:
        ids = [face.face_id for face in self.faces]
        valid = (
            bool(self.filename)
            and all(face.filename == self.filename for face in self.faces)
            and len(ids) == len(set(ids))
            and (
                self.status in _TERMINAL_PHOTO_STATUSES
                and self.technical_failure is None
                or self.status not in _TERMINAL_PHOTO_STATUSES
                and not self.faces
                and isinstance(self.technical_failure, str)
                and bool(self.technical_failure)
            )
        )
        if not valid:
            raise ValueError("invalid quality photo")


@dataclass(frozen=True)
class QualityRun:
    run_sha256: str
    inventory_sha256: str
    media_sha256: tuple[tuple[str, str], ...]
    generation_sha256: str
    photos: tuple[QualityPhoto, ...]
    model_hashes: tuple[tuple[str, str], ...]
    non_quality_configuration: Mapping[str, object]
    quality_configuration: Mapping[str, object]

    def __post_init__(self) -> None:
        filenames = [photo.filename for photo in self.photos]
        media_names = [filename for filename, _digest in self.media_sha256]
        if not (
            all(
                _SHA256.fullmatch(value)
                for value in (self.run_sha256, self.inventory_sha256, self.generation_sha256)
            )
            and self.media_sha256
            and self.media_sha256 == tuple(sorted(self.media_sha256))
            and media_names == sorted(media_names)
            and len(media_names) == len(set(media_names))
            and all(_SHA256.fullmatch(digest) for _filename, digest in self.media_sha256)
            and filenames == sorted(filenames)
            and len(filenames) == len(set(filenames))
            and filenames == media_names
            and self.model_hashes == tuple(sorted(self.model_hashes))
            and {name for name, _digest in self.model_hashes} == {"sface", "yunet"}
            and all(_SHA256.fullmatch(digest) for _name, digest in self.model_hashes)
            and self.non_quality_configuration
        ):
            raise ValueError("invalid quality run")
        _validate_quality_configuration(self.quality_configuration)
        object.__setattr__(
            self,
            "non_quality_configuration",
            MappingProxyType(dict(sorted(self.non_quality_configuration.items()))),
        )
        object.__setattr__(
            self, "quality_configuration", _freeze_mapping(self.quality_configuration)
        )


@dataclass(frozen=True)
class FaceMatch:
    filename: str
    baseline_face_id: str
    candidate_face_id: str
    intersection_over_union: float

    def __post_init__(self) -> None:
        if not (
            self.filename
            and self.baseline_face_id
            and self.candidate_face_id
            and math.isfinite(self.intersection_over_union)
            and 0 < self.intersection_over_union <= 1
        ):
            raise ValueError("invalid face match")


@dataclass(frozen=True)
class NewRejection:
    filename: str
    baseline_face_id: str
    candidate_face_id: str
    crop_path: str
    bounding_box: BoundingBox
    reasons: tuple[str, ...]
    confidence: float
    minimum_side_px: float
    relative_area: float
    sharpness: float

    def __post_init__(self) -> None:
        from photo_worker.face_quality import FaceQualityEvidence

        if not self.reasons:
            raise ValueError("invalid new rejection")
        FaceQualityEvidence(
            "normalized-laplacian-v1",
            112,
            self.confidence,
            self.minimum_side_px,
            self.relative_area,
            self.sharpness,
            "quality_rejected",
            self.reasons,
        )
        QualityFace(
            self.candidate_face_id,
            self.filename,
            self.bounding_box,
            self.crop_path,
            "quality_rejected",
            FaceQualityEvidence(
                "normalized-laplacian-v1",
                112,
                self.confidence,
                self.minimum_side_px,
                self.relative_area,
                self.sharpness,
                "quality_rejected",
                self.reasons,
            ),
        )


@dataclass(frozen=True)
class QualityOutcome:
    cohort: str
    filename: str
    face_id: str
    status: str
    embedded: bool
    crop_path: str
    confidence: float
    minimum_side_px: float
    relative_area: float
    sharpness: float
    reasons: tuple[str, ...]
    technical_failure: str | None

    def __post_init__(self) -> None:
        if self.cohort not in {"baseline", "candidate"}:
            raise ValueError("invalid quality outcome cohort")
        decision = "quality_rejected" if self.status == "quality_rejected" else "accepted"
        from photo_worker.face_quality import FaceQualityEvidence

        FaceQualityEvidence(
            "normalized-laplacian-v1",
            112,
            self.confidence,
            self.minimum_side_px,
            self.relative_area,
            self.sharpness,
            decision,
            self.reasons,
        )
        if (
            not self.filename
            or not self.face_id
            or not self.crop_path
            or self.status not in _FACE_STATUSES
            or self.embedded is not (self.status == "accepted")
            or ((self.status == "technical_failed") != isinstance(self.technical_failure, str))
            or (self.technical_failure is not None and not self.technical_failure)
        ):
            raise ValueError("invalid quality outcome")


@dataclass(frozen=True)
class ThresholdSample:
    metric: str
    face_id: str
    filename: str
    crop_path: str
    value: float
    threshold: float

    def __post_init__(self) -> None:
        if not (
            self.metric
            in {
                "minimum_face_px",
                "severe_blur_threshold",
                "borderline_blur_threshold",
                "minimum_relative_area",
                "minimum_confidence",
            }
            and self.face_id
            and self.filename
            and self.crop_path
            and math.isfinite(self.value)
            and math.isfinite(self.threshold)
        ):
            raise ValueError("invalid threshold sample")


@dataclass(frozen=True)
class TechnicalFailure:
    cohort: str
    filename: str
    face_id: str | None
    reason: str

    def __post_init__(self) -> None:
        if self.cohort not in {"baseline", "candidate"} or not self.filename or not self.reason:
            raise ValueError("invalid technical failure")


@dataclass(frozen=True)
class QualityComparison:
    baseline_run_sha256: str
    candidate_run_sha256: str
    inventory_sha256: str
    media_sha256: tuple[tuple[str, str], ...]
    baseline_generation_sha256: str
    candidate_generation_sha256: str
    minimum_iou: float
    quality_configuration: Mapping[str, object]
    counts: Mapping[str, int]
    baseline_outcomes: tuple[QualityOutcome, ...]
    candidate_outcomes: tuple[QualityOutcome, ...]
    matches: tuple[FaceMatch, ...]
    new_rejections: tuple[NewRejection, ...]
    threshold_band_fraction: float
    samples_per_metric: int
    threshold_samples: Mapping[str, tuple[ThresholdSample, ...]]
    metric_distributions: Mapping[str, Mapping[str, tuple[float, ...]]]
    rejection_reason_counts: Mapping[str, Mapping[str, int]]
    technical_reason_counts: Mapping[str, Mapping[str, int]]
    unresolved_photos: tuple[str, ...]
    technical_failures: tuple[TechnicalFailure, ...]

    def __post_init__(self) -> None:
        hashes = (
            self.baseline_run_sha256,
            self.candidate_run_sha256,
            self.inventory_sha256,
            self.baseline_generation_sha256,
            self.candidate_generation_sha256,
        )
        if any(not _SHA256.fullmatch(value) for value in hashes):
            raise ValueError("invalid quality comparison hashes")
        if not math.isfinite(self.minimum_iou) or not 0 < self.minimum_iou <= 1:
            raise ValueError("invalid comparison IoU")
        media_names = tuple(filename for filename, _digest in self.media_sha256)
        if (
            self.media_sha256 != tuple(sorted(self.media_sha256))
            or media_names != tuple(sorted(set(media_names)))
            or any(
                not filename or not _SHA256.fullmatch(digest)
                for filename, digest in self.media_sha256
            )
        ):
            raise ValueError("invalid quality comparison media")
        _validate_quality_configuration(self.quality_configuration)
        if self.matches != tuple(
            sorted(self.matches, key=lambda item: (item.filename, item.baseline_face_id))
        ) or self.new_rejections != tuple(
            sorted(self.new_rejections, key=lambda item: item.candidate_face_id)
        ):
            raise ValueError("quality comparison rows are unordered")
        baseline = {item.face_id: item for item in self.baseline_outcomes}
        candidate = {item.face_id: item for item in self.candidate_outcomes}
        if (
            len(baseline) != len(self.baseline_outcomes)
            or len(candidate) != len(self.candidate_outcomes)
            or tuple(baseline) != tuple(sorted(baseline))
            or tuple(candidate) != tuple(sorted(candidate))
        ):
            raise ValueError("quality comparison outcomes are invalid")
        matched_baseline = {item.baseline_face_id for item in self.matches}
        matched_candidate = {item.candidate_face_id for item in self.matches}
        if (
            len(matched_baseline) != len(self.matches)
            or len(matched_candidate) != len(self.matches)
            or not matched_baseline <= baseline.keys()
            or not matched_candidate <= candidate.keys()
            or any(
                baseline[item.baseline_face_id].filename != item.filename
                or candidate[item.candidate_face_id].filename != item.filename
                for item in self.matches
            )
        ):
            raise ValueError("quality comparison matches do not reconcile")
        match_pairs = {
            (item.filename, item.baseline_face_id, item.candidate_face_id) for item in self.matches
        }
        expected_rejection_pairs = {
            (item.filename, item.baseline_face_id, item.candidate_face_id)
            for item in self.matches
            if baseline[item.baseline_face_id].status == "accepted"
            and candidate[item.candidate_face_id].status == "quality_rejected"
        }
        if (
            any(
                (item.filename, item.baseline_face_id, item.candidate_face_id) not in match_pairs
                or baseline[item.baseline_face_id].status != "accepted"
                or candidate[item.candidate_face_id].status != "quality_rejected"
                or candidate[item.candidate_face_id].crop_path != item.crop_path
                or candidate[item.candidate_face_id].reasons != item.reasons
                or candidate[item.candidate_face_id].confidence != item.confidence
                or candidate[item.candidate_face_id].minimum_side_px != item.minimum_side_px
                or candidate[item.candidate_face_id].relative_area != item.relative_area
                or candidate[item.candidate_face_id].sharpness != item.sharpness
                for item in self.new_rejections
            )
            or {
                (item.filename, item.baseline_face_id, item.candidate_face_id)
                for item in self.new_rejections
            }
            != expected_rejection_pairs
        ):
            raise ValueError("new rejections do not reconcile")
        expected_counts = _comparison_counts(
            self.baseline_outcomes,
            self.candidate_outcomes,
            self.matches,
            self.new_rejections,
        )
        if dict(self.counts) != expected_counts:
            raise ValueError("quality comparison counts do not reconcile")
        expected_distributions = _metric_distributions(
            self.baseline_outcomes, self.candidate_outcomes
        )
        if _plain_nested(self.metric_distributions) != expected_distributions:
            raise ValueError("quality metric distributions do not reconcile")
        expected_reasons = _rejection_reason_counts(self.baseline_outcomes, self.candidate_outcomes)
        if _plain_nested(self.rejection_reason_counts) != expected_reasons:
            raise ValueError("quality rejection counts do not reconcile")
        expected_technical = _technical_reason_counts(self.technical_failures)
        if _plain_nested(self.technical_reason_counts) != expected_technical:
            raise ValueError("technical failure counts do not reconcile")
        unresolved = tuple(sorted(set(self.unresolved_photos)))
        unmatched_names = {
            outcome.filename
            for outcome in (*self.baseline_outcomes, *self.candidate_outcomes)
            if outcome.face_id
            not in (matched_baseline if outcome.cohort == "baseline" else matched_candidate)
        }
        technical_names = {item.filename for item in self.technical_failures}
        if self.unresolved_photos != unresolved or unmatched_names | technical_names != set(
            unresolved
        ):
            raise ValueError("unresolved evidence does not reconcile")
        ordered_failures = tuple(
            sorted(
                self.technical_failures,
                key=lambda item: (item.cohort, item.filename, item.face_id or "", item.reason),
            )
        )
        outcomes_by_cohort = {"baseline": baseline, "candidate": candidate}
        if (
            self.technical_failures != ordered_failures
            or len(set(ordered_failures)) != len(ordered_failures)
            or any(
                item.face_id is not None
                and (
                    item.face_id not in outcomes_by_cohort[item.cohort]
                    or outcomes_by_cohort[item.cohort][item.face_id].technical_failure
                    != item.reason
                )
                for item in self.technical_failures
            )
        ):
            raise ValueError("technical failures do not reconcile")
        retained_ids = {
            item.candidate_face_id
            for item in self.matches
            if baseline[item.baseline_face_id].status == "accepted"
            and candidate[item.candidate_face_id].status == "accepted"
        }
        if (
            not math.isfinite(self.threshold_band_fraction)
            or not 0 < self.threshold_band_fraction <= 1
            or isinstance(self.samples_per_metric, bool)
            or self.samples_per_metric < 1
            or dict(self.threshold_samples)
            != _expected_threshold_samples(
                candidate,
                retained_ids,
                self.quality_configuration,
                self.threshold_band_fraction,
                self.samples_per_metric,
            )
        ):
            raise ValueError("threshold samples do not reconcile")
        object.__setattr__(
            self, "quality_configuration", _freeze_mapping(self.quality_configuration)
        )
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))
        object.__setattr__(
            self, "threshold_samples", MappingProxyType(dict(self.threshold_samples))
        )
        object.__setattr__(
            self, "metric_distributions", _freeze_nested_mapping(self.metric_distributions)
        )
        object.__setattr__(
            self,
            "rejection_reason_counts",
            _freeze_nested_mapping(self.rejection_reason_counts),
        )
        object.__setattr__(
            self,
            "technical_reason_counts",
            _freeze_nested_mapping(self.technical_reason_counts),
        )


def compare_quality_runs(
    baseline: QualityRun,
    candidate: QualityRun,
    *,
    thresholds: FaceQualityThresholds,
    minimum_iou: float = 0.5,
    threshold_band_fraction: float = 0.1,
    samples_per_metric: int = 20,
) -> QualityComparison:
    """Compare exact source-equivalent runs without serializing their embeddings."""
    from photo_worker.face_quality import FaceQualityThresholds

    if not isinstance(baseline, QualityRun) or not isinstance(candidate, QualityRun):
        raise TypeError("quality runs are required")
    if baseline.inventory_sha256 != candidate.inventory_sha256:
        raise ValueError("inventory hashes differ")
    if baseline.media_sha256 != candidate.media_sha256:
        raise ValueError("media hashes differ")
    if baseline.model_hashes != candidate.model_hashes or dict(
        baseline.non_quality_configuration
    ) != dict(candidate.non_quality_configuration):
        raise ValueError("non-quality generation identity differs")
    candidate_configuration = {
        "algorithm_version": thresholds.algorithm_version,
        "crop_size": thresholds.crop_size,
        "minimum_face_px": thresholds.minimum_face_px,
        "severe_blur_threshold": thresholds.severe_blur_threshold,
        "borderline_blur_threshold": thresholds.borderline_blur_threshold,
        "minimum_relative_area": thresholds.minimum_relative_area,
        "minimum_confidence": thresholds.minimum_confidence,
    }
    if dict(candidate.quality_configuration) != candidate_configuration:
        raise ValueError("candidate quality configuration differs")
    if not isinstance(thresholds, FaceQualityThresholds):
        raise TypeError("production face-quality thresholds are required")
    if not 0 < minimum_iou <= 1:
        raise ValueError("minimum IoU is invalid")
    if not 0 < threshold_band_fraction <= 1:
        raise ValueError("threshold band fraction is invalid")
    if isinstance(samples_per_metric, bool) or samples_per_metric < 1:
        raise ValueError("sample count is invalid")

    baseline_by_name = {photo.filename: photo for photo in baseline.photos}
    candidate_by_name = {photo.filename: photo for photo in candidate.photos}
    if set(baseline_by_name) != set(candidate_by_name):
        raise ValueError("inventory photo sets differ")

    matches: list[FaceMatch] = []
    new_rejections: list[NewRejection] = []
    unmatched_baseline: set[str] = set()
    unmatched_candidate: set[str] = set()
    unresolved: set[str] = set()
    technical: list[TechnicalFailure] = []
    for filename in sorted(baseline_by_name):
        old_photo = baseline_by_name[filename]
        new_photo = candidate_by_name[filename]
        if old_photo.status not in _TERMINAL_PHOTO_STATUSES or new_photo.status not in (
            _TERMINAL_PHOTO_STATUSES
        ):
            unresolved.add(filename)
        if any(face.technical_failure is not None for face in old_photo.faces):
            unresolved.add(filename)
        if old_photo.technical_failure is not None:
            technical.append(
                TechnicalFailure("baseline", filename, None, old_photo.technical_failure)
            )
        for face in old_photo.faces:
            if face.technical_failure is not None:
                technical.append(
                    TechnicalFailure("baseline", filename, face.face_id, face.technical_failure)
                )
        if new_photo.technical_failure is not None:
            technical.append(
                TechnicalFailure("candidate", filename, None, new_photo.technical_failure)
            )
        for face in new_photo.faces:
            if face.technical_failure is not None:
                unresolved.add(filename)
                technical.append(
                    TechnicalFailure("candidate", filename, face.face_id, face.technical_failure)
                )
        photo_matches, old_only, new_only = _match_photo_faces(
            old_photo.faces, new_photo.faces, minimum_iou
        )
        matches.extend(
            FaceMatch(filename, old.face_id, new.face_id, overlap)
            for old, new, overlap in photo_matches
        )
        unmatched_baseline.update(face.face_id for face in old_only)
        unmatched_candidate.update(face.face_id for face in new_only)
        if old_only or new_only:
            unresolved.add(filename)
        for old, new, _overlap in photo_matches:
            if old.status == "accepted" and new.status == "quality_rejected":
                assert new.quality is not None
                new_rejections.append(
                    NewRejection(
                        filename,
                        old.face_id,
                        new.face_id,
                        new.crop_path,
                        new.bounding_box,
                        new.quality.reasons,
                        new.quality.confidence,
                        new.quality.minimum_side_px,
                        new.quality.relative_area,
                        new.quality.sharpness,
                    )
                )

    baseline_outcomes = tuple(
        sorted(
            (
                _quality_outcome("baseline", face)
                for photo in baseline.photos
                for face in photo.faces
            ),
            key=lambda item: item.face_id,
        )
    )
    candidate_outcomes = tuple(
        sorted(
            (
                _quality_outcome("candidate", face)
                for photo in candidate.photos
                for face in photo.faces
            ),
            key=lambda item: item.face_id,
        )
    )
    retained = tuple(
        new
        for old, new, _overlap in (
            pair
            for filename in sorted(baseline_by_name)
            for pair in _match_photo_faces(
                baseline_by_name[filename].faces,
                candidate_by_name[filename].faces,
                minimum_iou,
            )[0]
        )
        if old.status == "accepted" and new.status == "accepted"
    )
    samples = _threshold_samples(
        retained,
        thresholds,
        band_fraction=threshold_band_fraction,
        limit=samples_per_metric,
    )
    matches_value = tuple(sorted(matches, key=lambda item: (item.filename, item.baseline_face_id)))
    rejections_value = tuple(sorted(new_rejections, key=lambda item: item.candidate_face_id))
    counts = _comparison_counts(
        baseline_outcomes, candidate_outcomes, matches_value, rejections_value
    )
    return QualityComparison(
        baseline.run_sha256,
        candidate.run_sha256,
        baseline.inventory_sha256,
        baseline.media_sha256,
        baseline.generation_sha256,
        candidate.generation_sha256,
        minimum_iou,
        candidate_configuration,
        counts,
        baseline_outcomes,
        candidate_outcomes,
        matches_value,
        rejections_value,
        threshold_band_fraction,
        samples_per_metric,
        samples,
        _metric_distributions(baseline_outcomes, candidate_outcomes),
        _rejection_reason_counts(baseline_outcomes, candidate_outcomes),
        _technical_reason_counts(technical),
        tuple(sorted(unresolved)),
        tuple(
            sorted(
                technical,
                key=lambda item: (
                    item.cohort,
                    item.filename,
                    item.face_id or "",
                    item.reason,
                ),
            )
        ),
    )


def _match_photo_faces(
    baseline: Sequence[QualityFace],
    candidate: Sequence[QualityFace],
    minimum_iou: float,
) -> tuple[
    tuple[tuple[QualityFace, QualityFace, float], ...],
    tuple[QualityFace, ...],
    tuple[QualityFace, ...],
]:
    pairs = sorted(
        (
            (-overlap, old.face_id, new.face_id, old, new, overlap)
            for old in baseline
            for new in candidate
            if (overlap := _intersection_over_union(old.bounding_box, new.bounding_box))
            >= minimum_iou
        ),
        key=lambda item: item[:3],
    )
    used_old: set[str] = set()
    used_new: set[str] = set()
    matches: list[tuple[QualityFace, QualityFace, float]] = []
    for _negative_overlap, _old_id, _new_id, old, new, overlap in pairs:
        if old.face_id in used_old or new.face_id in used_new:
            continue
        used_old.add(old.face_id)
        used_new.add(new.face_id)
        matches.append((old, new, overlap))
    return (
        tuple(sorted(matches, key=lambda item: (item[0].face_id, item[1].face_id))),
        tuple(
            sorted(
                (face for face in baseline if face.face_id not in used_old),
                key=lambda face: face.face_id,
            )
        ),
        tuple(
            sorted(
                (face for face in candidate if face.face_id not in used_new),
                key=lambda face: face.face_id,
            )
        ),
    )


def _intersection_over_union(left: BoundingBox, right: BoundingBox) -> float:
    x1 = max(left.x, right.x)
    y1 = max(left.y, right.y)
    x2 = min(left.x + left.width, right.x + right.width)
    y2 = min(left.y + left.height, right.y + right.height)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if intersection == 0:
        return 0.0
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union


def _threshold_samples(
    faces: Sequence[QualityFace],
    thresholds: FaceQualityThresholds,
    *,
    band_fraction: float,
    limit: int,
) -> Mapping[str, tuple[ThresholdSample, ...]]:
    metrics = {
        "minimum_face_px": ("minimum_side_px", float(thresholds.minimum_face_px)),
        "severe_blur_threshold": ("sharpness", thresholds.severe_blur_threshold),
        "borderline_blur_threshold": ("sharpness", thresholds.borderline_blur_threshold),
        "minimum_relative_area": ("relative_area", thresholds.minimum_relative_area),
        "minimum_confidence": ("confidence", thresholds.minimum_confidence),
    }
    samples: dict[str, tuple[ThresholdSample, ...]] = {}
    for name, (attribute, threshold) in metrics.items():
        width = max(abs(threshold) * band_fraction, 1e-12)
        ranked = sorted(
            (
                (abs(float(getattr(face.quality, attribute)) - threshold), face)
                for face in faces
                if face.quality is not None
                and abs(float(getattr(face.quality, attribute)) - threshold) <= width
            ),
            key=lambda item: (item[0], item[1].face_id),
        )
        if ranked:
            samples[name] = tuple(
                ThresholdSample(
                    name,
                    face.face_id,
                    face.filename,
                    face.crop_path,
                    float(getattr(face.quality, attribute)),
                    threshold,
                )
                for _distance, face in ranked[:limit]
            )
    return dict(sorted(samples.items()))


def _quality_outcome(cohort: str, face: QualityFace) -> QualityOutcome:
    assert face.quality is not None
    return QualityOutcome(
        cohort,
        face.filename,
        face.face_id,
        face.status,
        face.status == "accepted",
        face.crop_path,
        face.quality.confidence,
        face.quality.minimum_side_px,
        face.quality.relative_area,
        face.quality.sharpness,
        face.quality.reasons,
        face.technical_failure,
    )


def _comparison_counts(
    baseline: Sequence[QualityOutcome],
    candidate: Sequence[QualityOutcome],
    matches: Sequence[FaceMatch],
    rejections: Sequence[NewRejection],
) -> dict[str, int]:
    values: dict[str, int] = {}
    for cohort, outcomes in (("baseline", baseline), ("candidate", candidate)):
        values[f"{cohort}_detected"] = len(outcomes)
        values[f"{cohort}_accepted"] = sum(item.status == "accepted" for item in outcomes)
        values[f"{cohort}_rejected"] = sum(item.status == "quality_rejected" for item in outcomes)
        values[f"{cohort}_embedded"] = sum(item.embedded for item in outcomes)
        values[f"{cohort}_technical_failures"] = sum(
            item.status == "technical_failed" for item in outcomes
        )
    baseline_by_id = {item.face_id: item for item in baseline}
    candidate_by_id = {item.face_id: item for item in candidate}
    values.update(
        {
            "matched": len(matches),
            "retained": sum(
                baseline_by_id[item.baseline_face_id].status == "accepted"
                and candidate_by_id[item.candidate_face_id].status == "accepted"
                for item in matches
            ),
            "newly_rejected": len(rejections),
            "old_only": len(baseline) - len(matches),
            "new_only": len(candidate) - len(matches),
        }
    )
    return values


def _metric_distributions(
    baseline: Sequence[QualityOutcome], candidate: Sequence[QualityOutcome]
) -> dict[str, dict[str, tuple[float, ...]]]:
    attributes = ("confidence", "minimum_side_px", "relative_area", "sharpness")
    return {
        cohort: {
            name: tuple(sorted(float(getattr(item, name)) for item in outcomes))
            for name in attributes
        }
        for cohort, outcomes in (("baseline", baseline), ("candidate", candidate))
    }


def _rejection_reason_counts(
    baseline: Sequence[QualityOutcome], candidate: Sequence[QualityOutcome]
) -> dict[str, dict[str, int]]:
    return {
        cohort: dict(
            sorted(
                Counter(
                    reason
                    for item in outcomes
                    if item.status == "quality_rejected"
                    for reason in item.reasons
                ).items()
            )
        )
        for cohort, outcomes in (("baseline", baseline), ("candidate", candidate))
    }


def _technical_reason_counts(
    failures: Sequence[TechnicalFailure],
) -> dict[str, dict[str, int]]:
    return {
        cohort: dict(
            sorted(Counter(item.reason for item in failures if item.cohort == cohort).items())
        )
        for cohort in ("baseline", "candidate")
    }


def _plain_nested(value: Mapping[str, Mapping[str, object]]) -> dict[str, dict[str, object]]:
    return {name: dict(items) for name, items in value.items()}


def _expected_threshold_samples(
    candidate: Mapping[str, QualityOutcome],
    retained_ids: set[str],
    configuration: Mapping[str, object],
    band_fraction: float,
    limit: int,
) -> dict[str, tuple[ThresholdSample, ...]]:
    metrics = {
        "minimum_face_px": (
            "minimum_side_px",
            _configuration_number(configuration["minimum_face_px"]),
        ),
        "severe_blur_threshold": (
            "sharpness",
            _configuration_number(configuration["severe_blur_threshold"]),
        ),
        "borderline_blur_threshold": (
            "sharpness",
            _configuration_number(configuration["borderline_blur_threshold"]),
        ),
        "minimum_relative_area": (
            "relative_area",
            _configuration_number(configuration["minimum_relative_area"]),
        ),
        "minimum_confidence": (
            "confidence",
            _configuration_number(configuration["minimum_confidence"]),
        ),
    }
    result: dict[str, tuple[ThresholdSample, ...]] = {}
    for metric, (attribute, threshold) in metrics.items():
        width = max(abs(threshold) * band_fraction, 1e-12)
        ranked = sorted(
            (abs(float(getattr(candidate[face_id], attribute)) - threshold), face_id)
            for face_id in retained_ids
            if abs(float(getattr(candidate[face_id], attribute)) - threshold) <= width
        )
        if ranked:
            result[metric] = tuple(
                ThresholdSample(
                    metric,
                    face_id,
                    candidate[face_id].filename,
                    candidate[face_id].crop_path,
                    float(getattr(candidate[face_id], attribute)),
                    threshold,
                )
                for _distance, face_id in ranked[:limit]
            )
    return dict(sorted(result.items()))


def _validate_quality_configuration(configuration: Mapping[str, object]) -> None:
    from photo_worker.face_quality import FaceQualityThresholds

    expected = {
        "algorithm_version",
        "crop_size",
        "minimum_face_px",
        "severe_blur_threshold",
        "borderline_blur_threshold",
        "minimum_relative_area",
        "minimum_confidence",
    }
    if set(configuration) != expected:
        raise ValueError("quality configuration is invalid")
    FaceQualityThresholds(
        algorithm_version=configuration["algorithm_version"],
        crop_size=configuration["crop_size"],
        minimum_face_px=configuration["minimum_face_px"],
        severe_blur_threshold=configuration["severe_blur_threshold"],
        borderline_blur_threshold=configuration["borderline_blur_threshold"],
        minimum_relative_area=configuration["minimum_relative_area"],
        minimum_confidence=configuration["minimum_confidence"],
    )


def _configuration_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("quality configuration number is invalid")
    return float(value)


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {
            key: _freeze_mapping(item)
            if isinstance(item, Mapping)
            else tuple(item)
            if isinstance(item, list)
            else item
            for key, item in sorted(value.items())
        }
    )


def _freeze_nested_mapping(
    value: Mapping[str, Mapping[str, object]],
) -> Mapping[str, Mapping[str, object]]:
    return MappingProxyType(
        {key: MappingProxyType(dict(items)) for key, items in sorted(value.items())}
    )
