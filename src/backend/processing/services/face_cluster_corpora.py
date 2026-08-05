"""Build and publish immutable event-scoped face-cluster corpora."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from time import monotonic
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from picflow.models import Event

from processing.models import (
    FACE_EMBEDDING_PROCESSOR,
    EventFaceClusterActivation,
    FaceCluster,
    FaceClusterCorpus,
    FaceClusterMember,
)
from processing.services.face_clustering import ClusterFace, build_face_clusters
from processing.services.face_cohort import CompatibleFaceEmbedding, load_compatible_face_embeddings

ALGORITHM_VERSION = "guarded-graph-v1"


def activate_face_cluster_corpus(
    *,
    event: Event,
    corpus: FaceClusterCorpus,
    configuration_hash: str,
    anchor_threshold: float,
    evaluation_report_hash: str,
    numeric_gates_reviewed: bool,
) -> EventFaceClusterActivation:
    """Atomically replace one event's explicit corpus-selection pointer.

    Building and evaluating corpora never call this function.  The operator must provide the
    reviewed report identity, the exact immutable corpus configuration hash, and a separately
    calibrated strong-anchor threshold for every activation.
    """
    direct_threshold = getattr(settings, "SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD", None)
    if (
        numeric_gates_reviewed is not True
        or not _is_sha256(configuration_hash)
        or not _is_sha256(evaluation_report_hash)
        or isinstance(anchor_threshold, bool)
        or not isinstance(anchor_threshold, (int, float))
        or not math.isfinite(anchor_threshold)
        or not 0 <= anchor_threshold <= 2
        or isinstance(direct_threshold, bool)
        or not isinstance(direct_threshold, (int, float))
        or not math.isfinite(direct_threshold)
        or not anchor_threshold < direct_threshold
    ):
        raise ValueError("invalid corpus activation")
    with transaction.atomic():
        locked_corpus = FaceClusterCorpus.objects.select_for_update().get(pk=corpus.pk)
        if (
            locked_corpus.event_id != event.pk
            or locked_corpus.status != FaceClusterCorpus.Status.PUBLISHED
            or locked_corpus.configuration_hash != configuration_hash
            or not _runtime_compatible(locked_corpus)
        ):
            raise ValueError("corpus cannot be activated")
        EventFaceClusterActivation.objects.select_for_update().filter(
            event=event, active=True
        ).update(active=False, deactivated_at=timezone.now())
        return EventFaceClusterActivation.objects.create(
            event=event,
            corpus=locked_corpus,
            active=True,
            anchor_threshold=float(anchor_threshold),
            configuration={
                "direct_threshold": float(direct_threshold),
                "anchor_threshold": float(anchor_threshold),
            },
            configuration_hash=configuration_hash,
            approved_evaluation_report_hash=evaluation_report_hash,
        )


def build_face_cluster_corpus(
    *,
    event: Event,
    version: int,
    edge_threshold: float,
    representative_threshold: float,
    distance_block_size: int,
    max_candidate_edges: int,
    generations: Sequence[Mapping[str, object]] | None = None,
    dimensions: int | None = None,
    algorithm_version: str = ALGORITHM_VERSION,
) -> FaceClusterCorpus:
    """Freeze an eligible cohort, build clusters, and atomically publish one version.

    A failed build remains as a ``failed`` corpus row, but no partial cluster/member rows remain.
    Publishing is the final write in the same transaction as all corpus rows, so readers can only
    select a complete corpus.
    """
    if generations is None:
        generations = _default_generations()
    if dimensions is None:
        configured_dimensions = getattr(settings, "SELFIE_SEARCH_EMBEDDING_DIMENSIONS", 128)
        dimensions = configured_dimensions if isinstance(configured_dimensions, int) else 128
    normalized_generations = tuple(dict(generation) for generation in generations)
    configuration = _configuration(
        algorithm_version=algorithm_version,
        generations=normalized_generations,
        dimensions=dimensions,
        edge_threshold=edge_threshold,
        representative_threshold=representative_threshold,
        distance_block_size=distance_block_size,
        max_candidate_edges=max_candidate_edges,
    )
    configuration_hash = _hash_json(configuration)
    model_version = _model_version(normalized_generations)
    processor_version = _processor_version(normalized_generations)
    contract_version = _contract_version(normalized_generations)

    corpus = FaceClusterCorpus.objects.create(
        event=event,
        version=version,
        status=FaceClusterCorpus.Status.BUILDING,
        algorithm_version=algorithm_version,
        configuration=configuration,
        configuration_hash=configuration_hash,
        contract_version=contract_version,
        processor_type=FACE_EMBEDDING_PROCESSOR,
        processor_version=processor_version,
        model_version=model_version,
        embedding_dimensions=dimensions,
        edge_threshold=edge_threshold,
        representative_threshold=representative_threshold,
        distance_block_size=distance_block_size,
        max_candidate_edges=max_candidate_edges,
        input_count=0,
        cluster_count=0,
        member_count=0,
        singleton_count=0,
        candidate_edge_count=0,
    )
    started = monotonic()

    try:
        rows = load_compatible_face_embeddings(event, normalized_generations, dimensions)
        input_hash = _input_hash(rows)
        cluster_faces = tuple(
            ClusterFace(face_id=row.detection_id, vector=row.vector) for row in rows
        )
        built_clusters = build_face_clusters(
            cluster_faces,
            edge_threshold=edge_threshold,
            representative_threshold=representative_threshold,
            distance_block_size=distance_block_size,
            max_candidate_edges=max_candidate_edges,
        )
        row_by_detection = {row.detection_id: row for row in rows}
        membership_hash = _membership_hash(built_clusters)
        singleton_count = sum(len(cluster.members) == 1 for cluster in built_clusters)
        with transaction.atomic():
            locked = FaceClusterCorpus.objects.select_for_update().get(pk=corpus.pk)
            cluster_rows = []
            member_rows = []
            for built_cluster in built_clusters:
                representative = built_cluster.representative_face_id
                source_row = row_by_detection[representative]
                if source_row.photo_event_id != event.pk or source_row.attempt_event_id != event.pk:
                    raise ValueError("cluster input escaped event")
                cluster_rows.append(
                    FaceCluster(
                        event=event,
                        corpus=locked,
                        cluster_key=built_cluster.cluster_key,
                        representative_detection_id=representative,
                        member_count=len(built_cluster.members),
                    )
                )
            FaceCluster.objects.bulk_create(cluster_rows)
            cluster_by_key = {cluster.cluster_key: cluster for cluster in locked.clusters.all()}
            for built_cluster in built_clusters:
                cluster = cluster_by_key[built_cluster.cluster_key]
                for member_index, member in enumerate(built_cluster.members):
                    source_row = row_by_detection[member.face_id]
                    if (
                        source_row.photo_event_id != event.pk
                        or source_row.attempt_event_id != event.pk
                    ):
                        raise ValueError("cluster input escaped event")
                    member_rows.append(
                        FaceClusterMember(
                            event=event,
                            corpus=locked,
                            cluster=cluster,
                            detection_id=member.face_id,
                            member_index=member_index,
                            distance_to_representative=member.distance_to_representative,
                        )
                    )
            FaceClusterMember.objects.bulk_create(member_rows)
            expected_member_count = sum(len(cluster.members) for cluster in built_clusters)
            if expected_member_count != len(rows) or len(member_rows) != len(rows):
                raise ValueError("cluster membership count identity failed")
            if locked.clusters.count() != len(built_clusters):
                raise ValueError("cluster count identity failed")
            locked.input_count = len(rows)
            locked.cluster_count = len(built_clusters)
            locked.member_count = len(member_rows)
            locked.singleton_count = singleton_count
            locked.candidate_edge_count = 0
            locked.input_hash = input_hash
            locked.membership_hash = membership_hash
            locked.build_duration_ms = max(0, int((monotonic() - started) * 1000))
            locked.status = FaceClusterCorpus.Status.PUBLISHED
            locked.published_at = timezone.now()
            locked.save(
                update_fields=[
                    "input_count",
                    "cluster_count",
                    "member_count",
                    "singleton_count",
                    "candidate_edge_count",
                    "input_hash",
                    "membership_hash",
                    "build_duration_ms",
                    "status",
                    "published_at",
                ]
            )
            corpus = locked
    except Exception as exc:
        _mark_failed(corpus, exc)
        raise
    return corpus


def _mark_failed(corpus: FaceClusterCorpus, error: Exception) -> None:
    FaceClusterCorpus.objects.filter(pk=corpus.pk, status=FaceClusterCorpus.Status.BUILDING).update(
        status=FaceClusterCorpus.Status.FAILED,
        failure_code=error.__class__.__name__[:64],
        failed_at=timezone.now(),
    )


def _configuration(
    *,
    algorithm_version: str,
    generations: Sequence[Mapping[str, object]],
    dimensions: int,
    edge_threshold: float,
    representative_threshold: float,
    distance_block_size: int,
    max_candidate_edges: int,
) -> dict[str, object]:
    return {
        "algorithm_version": algorithm_version,
        "embedding_dimensions": dimensions,
        "face_embedding_generations": [dict(generation) for generation in generations],
        "edge_threshold": edge_threshold,
        "representative_threshold": representative_threshold,
        "distance_block_size": distance_block_size,
        "max_candidate_edges": max_candidate_edges,
    }


def _default_generations() -> tuple[Mapping[str, object], ...]:
    from processing.services.enrollment import (
        CONTRACT_VERSION,
        FACE_EMBEDDING_CONFIGURATION,
        FACE_EMBEDDING_PROCESSOR_VERSION,
        PREVIEW_CONTRACT_VERSION,
        PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
    )

    configuration_hash = _hash_json(FACE_EMBEDDING_CONFIGURATION)
    return (
        {
            "contract_version": CONTRACT_VERSION,
            "processor_type": FACE_EMBEDDING_PROCESSOR,
            "processor_version": FACE_EMBEDDING_PROCESSOR_VERSION,
            "configuration": FACE_EMBEDDING_CONFIGURATION,
            "configuration_hash": configuration_hash,
            "model": FACE_EMBEDDING_CONFIGURATION["face_embedding"]["model"],  # type: ignore[index]
        },
        {
            "contract_version": PREVIEW_CONTRACT_VERSION,
            "processor_type": FACE_EMBEDDING_PROCESSOR,
            "processor_version": PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
            "configuration": FACE_EMBEDDING_CONFIGURATION,
            "configuration_hash": configuration_hash,
            "model": FACE_EMBEDDING_CONFIGURATION["face_embedding"]["model"],  # type: ignore[index]
        },
    )


def _model_version(generations: Sequence[Mapping[str, object]]) -> str:
    model = generations[0].get("model") if generations else None
    if not isinstance(model, str) or not model:
        raise ValueError("invalid face-embedding model")
    return model


def _processor_version(generations: Sequence[Mapping[str, object]]) -> int:
    value = generations[0].get("processor_version") if generations else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("invalid face-embedding processor version")
    return value


def _contract_version(generations: Sequence[Mapping[str, object]]) -> int:
    value = generations[0].get("contract_version") if generations else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("invalid face-embedding contract version")
    return value


def _input_hash(rows: Sequence[CompatibleFaceEmbedding]) -> str:
    payload = [
        {
            "detection_id": str(row.detection_id),
            "photo_id": row.photo_id,
            "model_version": row.model_version,
            "vector": list(row.vector),
            "contract_version": row.contract_version,
            "processor_version": row.processor_version,
            "configuration_hash": row.configuration_hash,
        }
        for row in rows
    ]
    return _hash_json(payload)


def _membership_hash(clusters: Sequence[Any]) -> str:
    payload = [
        {
            "cluster_key": cluster.cluster_key,
            "representative_face_id": str(cluster.representative_face_id),
            "members": [
                {
                    "face_id": str(member.face_id),
                    "distance": member.distance_to_representative,
                }
                for member in cluster.members
            ],
        }
        for cluster in clusters
    ]
    return _hash_json(payload)


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _runtime_compatible(corpus: FaceClusterCorpus) -> bool:
    from selfie_search.services.submission import _face_embedding_generations

    generations = list(_face_embedding_generations())
    return (
        corpus.model_version == getattr(settings, "SELFIE_SEARCH_EMBEDDING_MODEL", None)
        and corpus.embedding_dimensions
        == getattr(settings, "SELFIE_SEARCH_EMBEDDING_DIMENSIONS", None)
        and corpus.configuration.get("face_embedding_generations") == generations
    )
