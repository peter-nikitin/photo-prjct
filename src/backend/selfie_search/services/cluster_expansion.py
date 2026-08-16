"""Conservative, direct-first expansion over one activated face-cluster corpus."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from uuid import UUID

from django.db import DatabaseError
from face_cluster_contract import POLICY_ID, cluster_expansion_policy_hash
from picflow.gallery import gallery_photo_queryset
from processing.models import EventFaceClusterActivation, FaceClusterMember

from selfie_search.models import SelfieSearch
from selfie_search.services.ranking import RankedPhoto


class ClusterExpansionError(ValueError):
    """The optional corpus cannot safely widen this direct result."""


@dataclass(frozen=True, slots=True)
class ClusterEvidenceValue:
    cluster_id: UUID
    anchor_photo_id: str
    anchor_detection_id: UUID
    member_detection_id: UUID
    representative_distance: float
    source_order: int


@dataclass(frozen=True, slots=True)
class ExpandedRankedPhoto:
    photo_id: str
    primary_source: str
    direct: RankedPhoto | None
    cluster_evidence: tuple[ClusterEvidenceValue, ...]


@dataclass(frozen=True, slots=True)
class RankedPhotoExpansion:
    results: tuple[ExpandedRankedPhoto, ...]
    direct_matched_photo_count: int
    cluster_expanded_photo_count: int
    final_matched_photo_count: int
    strong_anchor_count: int
    expanded_cluster_count: int
    cluster_corpus_id: UUID | None
    cluster_corpus_version: int | None
    cluster_configuration_hash: str | None
    duration_ms: int
    outcome: str


def expand_ranked_photos(
    search: SelfieSearch,
    direct: tuple[RankedPhoto, ...],
    query: object,
    activation: EventFaceClusterActivation | None,
) -> RankedPhotoExpansion:
    """Return a complete immutable expansion plan, or the complete direct-only fallback.

    ``query`` remains only an explicit transient completion boundary.  Expansion uses the truthful
    direct distances and never writes or logs the query vector.
    """
    _ = query
    started = perf_counter()
    direct_results = tuple(
        ExpandedRankedPhoto(
            photo_id=row.photo_id,
            primary_source="direct",
            direct=row,
            cluster_evidence=(),
        )
        for row in direct
    )
    if activation is None:
        return _direct_only(direct_results, started, "corpus_unavailable")
    try:
        corpus = _compatible_corpus(search, activation)
        memberships = {
            member.detection_id: member
            for member in FaceClusterMember.objects.filter(
                corpus=corpus,
                detection_id__in=[row.detection_id for row in direct],
            ).select_related("cluster")
        }
        anchors = [
            (position, row, memberships[row.detection_id])
            for position, row in enumerate(direct, start=1)
            if (
                row.cosine_distance <= activation.anchor_threshold
                and row.detection_id in memberships
            )
        ]
        if not anchors:
            return _direct_only(
                direct_results,
                started,
                "no_strong_anchor",
                corpus_id=corpus.id,
                corpus_version=corpus.version,
                configuration_hash=activation.configuration_hash,
            )

        first_anchor_by_cluster: dict[UUID, tuple[int, RankedPhoto, FaceClusterMember]] = {}
        for anchor_row in anchors:
            cluster_id = anchor_row[2].cluster_id
            first_anchor_by_cluster.setdefault(cluster_id, anchor_row)
        selected = tuple(
            sorted(
                first_anchor_by_cluster.values(),
                key=lambda item: (
                    item[0],
                    item[1].cosine_distance,
                    item[1].photo_id,
                    str(item[2].id),
                ),
            )
        )
        members = (
            FaceClusterMember.objects.filter(
                corpus=corpus,
                cluster_id__in=[anchor[2].cluster_id for anchor in selected],
                detection__attempt__photo__in=gallery_photo_queryset(event=search.event),
            )
            .select_related("detection__attempt", "cluster")
            .order_by(
                "cluster_id",
                "distance_to_representative",
                "detection__attempt__photo_id",
                "detection_id",
            )
        )
        members_by_cluster: dict[UUID, list[FaceClusterMember]] = {}
        for member in members:
            members_by_cluster.setdefault(member.cluster_id, []).append(member)

        direct_by_photo = {row.photo_id: row for row in direct}
        evidence_by_photo: dict[str, list[ClusterEvidenceValue]] = {
            row.photo_id: [] for row in direct
        }
        appended: list[ExpandedRankedPhoto] = []
        seen = set(direct_by_photo)
        non_singleton_count = 0
        for source_order, (_position, anchor, anchor_member) in enumerate(selected, start=1):
            cluster_members = members_by_cluster.get(anchor_member.cluster_id, [])
            if anchor_member.cluster.member_count > 1:
                non_singleton_count += 1
            best_by_photo: dict[str, FaceClusterMember] = {}
            for member in cluster_members:
                photo_id = str(member.detection.attempt.photo_id)
                best_by_photo.setdefault(photo_id, member)
            for photo_id, member in sorted(
                best_by_photo.items(),
                key=lambda item: (item[1].distance_to_representative, item[0]),
            ):
                evidence = ClusterEvidenceValue(
                    cluster_id=anchor_member.cluster_id,
                    anchor_photo_id=anchor.photo_id,
                    anchor_detection_id=anchor.detection_id,
                    member_detection_id=member.detection_id,
                    representative_distance=float(member.distance_to_representative),
                    source_order=source_order,
                )
                evidence_by_photo.setdefault(photo_id, []).append(evidence)
                if photo_id not in seen:
                    seen.add(photo_id)
                    appended.append(
                        ExpandedRankedPhoto(
                            photo_id=photo_id,
                            primary_source="face_cluster_expansion",
                            direct=None,
                            cluster_evidence=(),
                        )
                    )
        results = tuple(
            ExpandedRankedPhoto(
                photo_id=row.photo_id,
                primary_source=row.primary_source,
                direct=row.direct,
                cluster_evidence=tuple(evidence_by_photo.get(row.photo_id, ())),
            )
            for row in (*direct_results, *appended)
        )
        expanded_count = len(appended)
        return RankedPhotoExpansion(
            results=results,
            direct_matched_photo_count=len(direct),
            cluster_expanded_photo_count=expanded_count,
            final_matched_photo_count=len(results),
            strong_anchor_count=len(anchors),
            expanded_cluster_count=non_singleton_count,
            cluster_corpus_id=corpus.id,
            cluster_corpus_version=corpus.version,
            cluster_configuration_hash=activation.configuration_hash,
            duration_ms=_duration_ms(started),
            outcome="expanded" if expanded_count else "no_new_photos",
        )
    except (ClusterExpansionError, DatabaseError):
        return _direct_only(direct_results, started, "corpus_incompatible")


def direct_only_ranked_photos(
    direct: tuple[RankedPhoto, ...], *, outcome: str
) -> RankedPhotoExpansion:
    """Build the complete direct snapshot when optional expansion is unavailable."""
    return _direct_only(
        tuple(
            ExpandedRankedPhoto(
                photo_id=row.photo_id,
                primary_source="direct",
                direct=row,
                cluster_evidence=(),
            )
            for row in direct
        ),
        perf_counter(),
        outcome,
    )


def _compatible_corpus(search: SelfieSearch, activation: EventFaceClusterActivation):
    corpus = activation.corpus
    configuration = search.configuration
    if not isinstance(configuration, dict) or not isinstance(corpus.configuration, dict):
        raise ClusterExpansionError("cluster corpus is incompatible")
    threshold = configuration.get("cosine_distance_threshold")
    anchor_threshold = activation.anchor_threshold
    policy = activation.configuration
    reviewed_direct_threshold = policy.get("direct_threshold") if isinstance(policy, dict) else None
    reviewed_anchor_threshold = policy.get("anchor_threshold") if isinstance(policy, dict) else None
    reviewed_corpus_hash = (
        policy.get("corpus_configuration_hash") if isinstance(policy, dict) else None
    )
    policy_id = policy.get("policy_id") if isinstance(policy, dict) else None
    if (
        not activation.active
        or activation.event_id != search.event_id
        or corpus.event_id != search.event_id
        or corpus.status != corpus.Status.PUBLISHED
        or corpus.model_version != configuration.get("embedding_model")
        or corpus.embedding_dimensions != configuration.get("embedding_dimensions")
        or corpus.configuration.get("face_embedding_generations")
        != configuration.get("gallery_face_embedding_generations")
        or isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not isfinite(threshold)
        or isinstance(anchor_threshold, bool)
        or not isinstance(anchor_threshold, (int, float))
        or not isfinite(anchor_threshold)
        or anchor_threshold >= threshold
        or policy_id != POLICY_ID
        or reviewed_direct_threshold != threshold
        or reviewed_anchor_threshold != anchor_threshold
        or reviewed_corpus_hash != corpus.configuration_hash
        or activation.configuration_hash
        != cluster_expansion_policy_hash(corpus.configuration_hash, threshold, anchor_threshold)
    ):
        raise ClusterExpansionError("cluster corpus is incompatible")
    return corpus


def _direct_only(
    results: tuple[ExpandedRankedPhoto, ...],
    started: float,
    outcome: str,
    *,
    corpus_id: UUID | None = None,
    corpus_version: int | None = None,
    configuration_hash: str | None = None,
) -> RankedPhotoExpansion:
    return RankedPhotoExpansion(
        results=results,
        direct_matched_photo_count=len(results),
        cluster_expanded_photo_count=0,
        final_matched_photo_count=len(results),
        strong_anchor_count=0,
        expanded_cluster_count=0,
        cluster_corpus_id=corpus_id,
        cluster_corpus_version=corpus_version,
        cluster_configuration_hash=configuration_hash,
        duration_ms=_duration_ms(started),
        outcome=outcome,
    )


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1_000))
