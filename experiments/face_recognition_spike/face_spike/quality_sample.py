"""Pure deterministic sampling and weighted analysis of quality rejections."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from .quality_comparison import NewRejection, QualityComparison

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LABELS = ("clear", "blurred", "unusably_small", "uncertain")
_Z_95 = 1.959963984540054


@dataclass(frozen=True)
class QualitySampleStratum:
    reasons: tuple[str, ...]
    population_count: int
    sample_count: int

    def __post_init__(self) -> None:
        if not (
            self.reasons
            and all(isinstance(reason, str) and reason for reason in self.reasons)
            and _positive_int(self.population_count)
            and _positive_int(self.sample_count)
            and self.sample_count <= self.population_count
        ):
            raise ValueError("invalid quality sample stratum")

    @property
    def inclusion_weight(self) -> float:
        return self.population_count / self.sample_count


@dataclass(frozen=True)
class SampledRejection:
    rejection: NewRejection
    population_count: int
    sample_count: int

    def __post_init__(self) -> None:
        if not (
            isinstance(self.rejection, NewRejection)
            and _positive_int(self.population_count)
            and _positive_int(self.sample_count)
            and self.sample_count <= self.population_count
        ):
            raise ValueError("invalid sampled rejection")

    @property
    def face_id(self) -> str:
        return self.rejection.candidate_face_id

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.rejection.reasons

    @property
    def inclusion_weight(self) -> float:
        return self.population_count / self.sample_count


@dataclass(frozen=True)
class QualitySample:
    source_bundle_sha256: str
    population_count: int
    strata: tuple[QualitySampleStratum, ...]
    rejections: tuple[SampledRejection, ...]

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.source_bundle_sha256):
            raise ValueError("invalid quality sample bundle hash")
        if not self.strata or not _positive_int(self.population_count):
            raise ValueError("invalid quality sample population")
        if self.strata != tuple(sorted(self.strata, key=lambda item: item.reasons)):
            raise ValueError("quality sample strata are unordered")
        if len({item.reasons for item in self.strata}) != len(self.strata):
            raise ValueError("quality sample strata are duplicated")
        if sum(item.population_count for item in self.strata) != self.population_count:
            raise ValueError("quality sample population does not reconcile")
        if sum(item.sample_count for item in self.strata) != len(self.rejections):
            raise ValueError("quality sample count does not reconcile")
        by_reasons = {item.reasons: item for item in self.strata}
        if len({item.face_id for item in self.rejections}) != len(self.rejections):
            raise ValueError("quality sample faces are duplicated")
        sampled_counts = Counter(item.reasons for item in self.rejections)
        if any(
            item.reasons not in by_reasons
            or item.population_count != by_reasons[item.reasons].population_count
            or item.sample_count != by_reasons[item.reasons].sample_count
            for item in self.rejections
        ) or dict(sampled_counts) != {item.reasons: item.sample_count for item in self.strata}:
            raise ValueError("quality sample strata do not reconcile")

    @property
    def sample_count(self) -> int:
        return len(self.rejections)


@dataclass(frozen=True)
class QualitySampleLabel:
    face_id: str
    label: str

    def __post_init__(self) -> None:
        if not self.face_id or self.label not in _LABELS:
            raise ValueError("invalid quality sample label")


@dataclass(frozen=True)
class QualitySampleAnalysis:
    raw_counts: Mapping[str, int]
    weighted_counts: Mapping[str, float]
    weighted_proportions: Mapping[str, float]
    kish_effective_sample_size: float
    clear_wilson_interval: tuple[float, float]
    clear_rejections: tuple[SampledRejection, ...]
    uncertain_rejections: tuple[SampledRejection, ...]

    def __post_init__(self) -> None:
        raw = dict(self.raw_counts)
        weighted = dict(self.weighted_counts)
        proportions = dict(self.weighted_proportions)
        lower, upper = self.clear_wilson_interval
        if not (
            set(raw) == set(weighted) == set(proportions) == set(_LABELS)
            and all(isinstance(value, int) and value >= 0 for value in raw.values())
            and all(math.isfinite(value) and value >= 0 for value in weighted.values())
            and all(math.isfinite(value) and 0 <= value <= 1 for value in proportions.values())
            and math.isfinite(self.kish_effective_sample_size)
            and self.kish_effective_sample_size > 0
            and math.isfinite(lower)
            and math.isfinite(upper)
            and 0 <= lower <= upper <= 1
        ):
            raise ValueError("invalid quality sample analysis")
        object.__setattr__(self, "raw_counts", MappingProxyType(raw))
        object.__setattr__(self, "weighted_counts", MappingProxyType(weighted))
        object.__setattr__(self, "weighted_proportions", MappingProxyType(proportions))


def build_quality_sample(
    comparison: QualityComparison, bundle_sha256: str, sample_size: int = 1506
) -> QualitySample:
    """Allocate and select a deterministic stratified rejection sample."""
    if not _SHA256.fullmatch(bundle_sha256):
        raise ValueError("invalid comparison bundle hash")
    if not _positive_int(sample_size):
        raise ValueError("invalid sample size")
    rejections = tuple(comparison.new_rejections)
    if not rejections:
        raise ValueError("quality rejection population is empty")
    if not all(isinstance(item, NewRejection) for item in rejections):
        raise TypeError("quality comparison rejections are required")
    if len({item.candidate_face_id for item in rejections}) != len(rejections):
        raise ValueError("quality rejection faces are duplicated")
    if sample_size > len(rejections):
        raise ValueError("sample size exceeds population")

    populations = {
        reasons: tuple(sorted(items, key=lambda item: item.candidate_face_id))
        for reasons, items in _group_rejections(rejections).items()
    }
    if sample_size < len(populations):
        raise ValueError("sample size cannot represent every stratum")
    allocations = _allocate_sample_counts(
        {reasons: len(items) for reasons, items in populations.items()}, sample_size
    )
    strata = tuple(
        QualitySampleStratum(reasons, len(populations[reasons]), allocations[reasons])
        for reasons in sorted(populations)
    )
    selected: list[SampledRejection] = []
    for stratum in strata:
        ranked = sorted(
            populations[stratum.reasons],
            key=lambda item: (
                _selection_digest(bundle_sha256, item.candidate_face_id),
                item.candidate_face_id,
            ),
        )
        selected.extend(
            SampledRejection(item, stratum.population_count, stratum.sample_count)
            for item in ranked[: stratum.sample_count]
        )
    return QualitySample(bundle_sha256, len(rejections), strata, tuple(selected))


def analyze_quality_sample(
    sample: QualitySample, labels: Sequence[QualitySampleLabel]
) -> QualitySampleAnalysis:
    """Calculate weighted estimates from one complete manual sample."""
    if not isinstance(sample, QualitySample):
        raise TypeError("quality sample is required")
    values = tuple(labels)
    if not all(isinstance(item, QualitySampleLabel) for item in values):
        raise TypeError("quality sample labels are required")
    label_ids = [item.face_id for item in values]
    if len(set(label_ids)) != len(label_ids):
        raise ValueError("quality sample labels are duplicated")
    sample_ids = {item.face_id for item in sample.rejections}
    if set(label_ids) != sample_ids:
        raise ValueError("quality sample labels are incomplete")

    labels_by_id = {item.face_id: item.label for item in values}
    raw = {label: 0 for label in _LABELS}
    weighted = {label: 0.0 for label in _LABELS}
    weights: list[float] = []
    clear_rejections: list[SampledRejection] = []
    uncertain_rejections: list[SampledRejection] = []
    for rejection in sample.rejections:
        label = labels_by_id[rejection.face_id]
        weight = rejection.inclusion_weight
        raw[label] += 1
        weighted[label] += weight
        weights.append(weight)
        if label == "clear":
            clear_rejections.append(rejection)
        elif label == "uncertain":
            uncertain_rejections.append(rejection)
    proportions = {label: weighted[label] / sample.population_count for label in _LABELS}
    effective_size = sum(weights) ** 2 / sum(weight * weight for weight in weights)
    interval = _wilson_interval(proportions["clear"], effective_size)
    return QualitySampleAnalysis(
        raw,
        weighted,
        proportions,
        effective_size,
        interval,
        tuple(clear_rejections),
        tuple(uncertain_rejections),
    )


def _group_rejections(
    rejections: Sequence[NewRejection],
) -> dict[tuple[str, ...], list[NewRejection]]:
    grouped: dict[tuple[str, ...], list[NewRejection]] = {}
    for rejection in rejections:
        grouped.setdefault(rejection.reasons, []).append(rejection)
    return grouped


def _allocate_sample_counts(
    populations: Mapping[tuple[str, ...], int], sample_size: int
) -> dict[tuple[str, ...], int]:
    allocations = {reasons: 1 for reasons in populations}
    remaining = sample_size - len(populations)
    capacities = {reasons: count - 1 for reasons, count in populations.items()}
    capacity_total = sum(capacities.values())
    if remaining > capacity_total:
        raise ValueError("sample allocation exceeds population capacity")
    if not remaining:
        return allocations
    quotas = {
        reasons: remaining * capacity / capacity_total for reasons, capacity in capacities.items()
    }
    floor_allocations = {reasons: math.floor(quota) for reasons, quota in quotas.items()}
    for reasons, count in floor_allocations.items():
        allocations[reasons] += count
    leftovers = remaining - sum(floor_allocations.values())
    for reasons in sorted(quotas, key=lambda item: (-(quotas[item] % 1), item))[:leftovers]:
        allocations[reasons] += 1
    if any(allocations[reasons] > populations[reasons] for reasons in populations):
        raise ValueError("sample allocation exceeds stratum capacity")
    return allocations


def _selection_digest(bundle_sha256: str, face_id: str) -> str:
    return hashlib.sha256(f"{bundle_sha256}\0{face_id}".encode()).hexdigest()


def _wilson_interval(proportion: float, effective_size: float) -> tuple[float, float]:
    bounded = min(1.0, max(0.0, proportion))
    denominator = 1 + _Z_95**2 / effective_size
    center = (bounded + _Z_95**2 / (2 * effective_size)) / denominator
    half_width = (
        _Z_95
        * math.sqrt(bounded * (1 - bounded) / effective_size + _Z_95**2 / (4 * effective_size**2))
        / denominator
    )
    return (max(0.0, center - half_width), min(1.0, center + half_width))


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
