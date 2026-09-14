"""One process-local, immutable gallery matrix with authoritative membership validation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from uuid import UUID

import numpy as np
from numpy.typing import NDArray
from picflow.models import Event
from processing.services.face_cohort import (
    FaceCohortIdentity,
    face_cohort_identity_order,
    iter_compatible_face_cohort,
    load_compatible_face_identities,
)

from selfie_search.services.ranking import RankingError, _normalized_vector


@dataclass(frozen=True, slots=True)
class CohortFace:
    detection_id: UUID
    photo_id: str


@dataclass(frozen=True, slots=True)
class _CohortKey:
    event_id: object
    model: str
    dimensions: int
    generations: str


@dataclass(frozen=True, slots=True)
class CohortCacheEntry:
    key: _CohortKey
    fingerprint: tuple[FaceCohortIdentity, ...]
    faces: tuple[CohortFace, ...]
    matrix: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class CohortCacheLookup:
    entry: CohortCacheEntry
    cache_hit: bool
    identity_ms: float
    build_ms: float


class CohortCache:
    """Serialize builds only; each lookup proves eligibility using a fresh scalar read."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._entry: CohortCacheEntry | None = None

    def get(
        self,
        *,
        event: Event,
        generations: Sequence[Mapping[str, object]],
        model: str,
        dimensions: int,
    ) -> CohortCacheLookup:
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
            raise RankingError("embedding dimensions must be positive")
        if not isinstance(model, str) or not model:
            raise RankingError("candidate embedding model is incompatible")
        # Freeze the complete configuration, including nested values, before either query.
        frozen_generations = json.dumps(
            generations, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        key = _CohortKey(event.pk, model, dimensions, frozen_generations)
        generation_snapshot = json.loads(frozen_generations)
        started = perf_counter()
        fingerprint = load_compatible_face_identities(event, generation_snapshot)
        identity_ms = (perf_counter() - started) * 1_000
        entry = self._entry
        if entry is not None and entry.key == key and entry.fingerprint == fingerprint:
            return CohortCacheLookup(entry, True, identity_ms, 0.0)
        with self._lock:
            entry = self._entry
            if entry is not None and entry.key == key and entry.fingerprint == fingerprint:
                return CohortCacheLookup(entry, True, identity_ms, 0.0)
            started = perf_counter()
            entry = _build_entry(event, generation_snapshot, key, len(fingerprint))
            self._entry = entry
            return CohortCacheLookup(entry, False, identity_ms, (perf_counter() - started) * 1_000)


def _build_entry(
    event: Event,
    generations: Sequence[Mapping[str, object]],
    key: _CohortKey,
    expected_count: int,
) -> CohortCacheEntry:
    matrix = np.empty((expected_count, key.dimensions), dtype=np.float64)
    identities: list[FaceCohortIdentity] = []
    for identity, raw_vector in iter_compatible_face_cohort(event, generations):
        if (
            identity.photo_event_id != key.event_id
            or identity.attempt_event_id != key.event_id
            or identity.attempt_photo_id != identity.photo_id
            or not isinstance(identity.photo_id, str)
            or not all(
                isinstance(value, UUID)
                for value in (
                    identity.projection_id,
                    identity.embedding_id,
                    identity.detection_id,
                    identity.attempt_id,
                    identity.job_id,
                    identity.run_id,
                )
            )
        ):
            raise RankingError("candidate identity is outside the frozen search event")
        if identity.model_version != key.model:
            raise RankingError("candidate embedding model is incompatible")
        vector = _normalized_vector(raw_vector, dimensions=key.dimensions, error_type=RankingError)
        index = len(identities)
        if index == len(matrix):
            matrix.resize((max(index + 1, index * 2), key.dimensions), refcheck=False)
        matrix[index] = vector
        identities.append(identity)
    order = sorted(
        range(len(identities)), key=lambda index: face_cohort_identity_order(identities[index])
    )
    fingerprint = tuple(identities[index] for index in order)
    # An immutable bytes owner prevents callers from re-enabling NumPy writes. No vectors
    # or query data survive separately from the single contiguous float64 matrix.
    immutable_matrix = np.frombuffer(matrix[order].tobytes(), dtype=np.float64).reshape(
        len(fingerprint), key.dimensions
    )
    return CohortCacheEntry(
        key=key,
        fingerprint=fingerprint,
        faces=tuple(
            CohortFace(identity.detection_id, identity.photo_id) for identity in fingerprint
        ),
        matrix=immutable_matrix,
    )


cohort_cache = CohortCache()
