"""Queue one follow-up against currently visible, unambiguous saved faces."""

import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import DatabaseError, connection, transaction
from django.db.models import Exists, F, OuterRef, QuerySet, Subquery
from django.views.decorators.debug import sensitive_variables
from feature_flags.registry import APPAREL_SEARCH_EXPANSION
from feature_flags.services import is_enabled, is_enabled_for_state
from feature_flags.states import FEATURE_FLAG_STAFF
from picflow.gallery import retrieval_gallery_photo_queryset
from picflow.models import Event
from processing.models import FaceEmbeddingVector, PhotoFaceDetection, PhotoProcessingState
from psycopg import Cursor

from selfie_search.models import (
    ANCHOR_EXPANSION_STAFF_ELIGIBILITY_KEY,
    FaceSearchTask,
    SelfieSearch,
)
from selfie_search.services.apparel_expansion import continuation_apparel_neighbors
from selfie_search.services.jobs import enqueue_face_search
from selfie_search.services.negative_feedback import negative_feedback_state
from selfie_search.services.ranking import RankingError
from selfie_search.services.results import eligible_saved_result_rows
from selfie_search.services.submission import (
    GallerySearchUnavailable,
    _configuration_hash,
    _gallery_configuration,
    _gallery_source_vector,
    _token_digest,
    _usable_gallery_vectors,
)

_AUTO_SOURCE = "continuation_centroids"
_MANUAL_SOURCE = "gallery_photo"
_MAX_CENTROIDS = 3
_MAX_MEMBERS = 5
_MAX_CANDIDATES = 60
_STRONG_DISTANCE = 0.38
_CONSISTENCY_DISTANCE = 0.35
_SHOW_PAID_PREVIEWS_KEY = "continuation_paid_watermarked_previews"


class ContinuationUnavailable(LookupError):
    """The requested result face is unavailable or the search has no unused eligible anchors."""


@dataclass(frozen=True, slots=True)
class Anchor:
    photo_id: str
    detection_id: UUID


def _manual_source_photo_id(search: SelfieSearch) -> str | None:
    source = search.configuration.get("query_source")
    if isinstance(source, dict) and source.get("kind") == _MANUAL_SOURCE:
        photo_id = source.get("photo_id")
        if isinstance(photo_id, str):
            return photo_id
    return None


def is_automatic_continuation(search: SelfieSearch) -> bool:
    source = search.configuration.get("query_source")
    return isinstance(source, dict) and source.get("kind") == _AUTO_SOURCE


def _configured_centroids(search: SelfieSearch) -> tuple[tuple[Anchor, ...], ...]:
    source = search.configuration.get("query_source")
    if not isinstance(source, dict) or source.get("kind") != _AUTO_SOURCE:
        return ()
    centroids = source.get("centroids")
    if not isinstance(centroids, list) or not 1 <= len(centroids) <= _MAX_CENTROIDS:
        raise RankingError("invalid continuation centroids")

    def anchors() -> Iterator[tuple[Anchor, ...]]:
        for group in centroids:
            if not isinstance(group, dict):
                raise RankingError("invalid continuation centroid")
            photo_ids, detection_ids = group.get("photo_ids"), group.get("detection_ids")
            if (
                not isinstance(photo_ids, list)
                or not isinstance(detection_ids, list)
                or not 1 <= len(photo_ids) <= _MAX_MEMBERS
                or len(photo_ids) != len(detection_ids)
                or not all(isinstance(item, str) for item in (*photo_ids, *detection_ids))
            ):
                raise RankingError("invalid continuation anchors")
            try:
                yield tuple(
                    Anchor(photo_id, UUID(detection_id))
                    for photo_id, detection_id in zip(photo_ids, detection_ids, strict=True)
                )
            except ValueError:
                raise RankingError("invalid continuation detections") from None

    groups = tuple(anchors())
    photos = [anchor.photo_id for group in groups for anchor in group]
    if len(photos) != len(set(photos)):
        raise RankingError("continuation contains duplicate anchors")
    return groups


def consumed_anchor_photo_ids(search: SelfieSearch, *, include_automatic: bool = True) -> set[str]:
    consumed = set()
    root_gallery_seed = _manual_source_photo_id(search)
    if root_gallery_seed is not None:
        consumed.add(root_gallery_seed)
    for child in search.continuation_passes.filter(status=SelfieSearch.Status.READY).only(
        "configuration"
    ):
        manual = _manual_source_photo_id(child)
        if manual is not None:
            consumed.add(manual)
        elif include_automatic:
            consumed.update(
                anchor.photo_id for group in _configured_centroids(child) for anchor in group
            )
    return consumed


def _current_single_faces(
    *, event: Event, photo_ids: QuerySet, model: object
) -> QuerySet[FaceEmbeddingVector]:
    accepted_attempt_ids = (
        PhotoProcessingState.objects.filter(
            photo_id__in=Subquery(photo_ids),
            processor_type="face_embedding",
            accepted_attempt__event=event,
            accepted_attempt__photo_id=F("photo_id"),
            accepted_attempt__accepted=True,
        )
        .order_by()
        .values("accepted_attempt_id")
    )
    second_detection = PhotoFaceDetection.objects.filter(
        attempt_id=OuterRef("detection__attempt_id")
    ).exclude(pk=OuterRef("detection_id"))
    return (
        _usable_gallery_vectors(accepted_attempt_ids=accepted_attempt_ids, model=model)
        .filter(~Exists(second_detection))
        .order_by()
    )


def available_manual_photo_ids(search: SelfieSearch, *, photo_ids: tuple[str, ...]) -> set[str]:
    """Return unused single-face anchors from a page of already eligible saved photos."""
    unused = set(photo_ids) - consumed_anchor_photo_ids(search, include_automatic=False)
    if not unused:
        return set()
    model = _gallery_configuration(event=search.event)["embedding_model"]
    faces = _current_single_faces(
        event=search.event,
        photo_ids=retrieval_gallery_photo_queryset(event=search.event)
        .filter(pk__in=unused)
        .order_by()
        .values("pk"),
        model=model,
    )
    return set(faces.values_list("detection__attempt__photo_id", flat=True))


# A weak match can join the pool only when two independently trusted saved faces support it.
# The bounded proposals and trust cohorts keep pairwise comparisons at a fixed upper bound.
_ANCHOR_SQL = """
WITH current_faces(photo_id, detection_id, embedding) AS (
    {faces_sql}
),
saved AS (
    SELECT DISTINCT ON (result.photo_id, face.detection_id)
        result.photo_id, face.detection_id, face.embedding,
        coalesce(direct.cosine_distance, expanded.cosine_distance) AS distance,
        coalesce(direct.cosine_distance <= %s AND direct.centroid_index IS NULL, false)
            AS strong
    FROM selfie_search_selfiesearchresult result
    JOIN current_faces face USING (photo_id)
    LEFT JOIN selfie_search_selfiesearchdirectevidence direct
        ON direct.result_id = result.id AND direct.detection_id = face.detection_id
    LEFT JOIN selfie_search_selfiesearchanchorevidence expanded
        ON expanded.result_id = result.id AND expanded.detection_id = face.detection_id
    WHERE result.id IN ({eligible_sql})
        AND (direct.id IS NOT NULL OR expanded.id IS NOT NULL)
        AND vector_norm(face.embedding) > 0
    ORDER BY result.photo_id, face.detection_id, strong DESC, distance
),
trusted AS (
    SELECT * FROM saved
    WHERE strong
    ORDER BY distance, photo_id
    LIMIT %s
),
proposed AS (
    SELECT * FROM saved
    WHERE NOT (photo_id = ANY(%s::varchar[]))
    ORDER BY strong DESC, distance, photo_id
    LIMIT %s
),
candidates AS MATERIALIZED (
    SELECT proposed.* FROM proposed
    WHERE strong OR (
        SELECT count(*) FROM trusted
        WHERE trusted.photo_id <> proposed.photo_id
            AND (proposed.embedding <=> trusted.embedding) <= %s
    ) >= 2
)
SELECT candidate.photo_id, candidate.detection_id,
    ARRAY(
        SELECT other.photo_id FROM candidates other
        WHERE other.photo_id <> candidate.photo_id
            AND (candidate.embedding <=> other.embedding) <= %s
    )
FROM candidates candidate
ORDER BY candidate.strong DESC, candidate.distance, candidate.photo_id
"""


def _select_centroids(
    *,
    faces: QuerySet[FaceEmbeddingVector],
    eligible: QuerySet,
    consumed: set[str],
    root: SelfieSearch,
    model: str,
    apparel_enabled: bool,
) -> tuple[tuple[Anchor, ...], ...]:
    faces_sql, faces_params = faces.values_list(
        "detection__attempt__photo_id", "detection_id", "vector"
    ).query.sql_with_params()
    eligible_sql, eligible_params = eligible.order_by().values("id").query.sql_with_params()
    with connection.wrap_database_errors, Cursor(connection.connection) as cursor:
        cursor.execute(
            _ANCHOR_SQL.format(faces_sql=faces_sql, eligible_sql=eligible_sql),
            [
                *faces_params,
                _STRONG_DISTANCE,
                *eligible_params,
                _MAX_CANDIDATES,
                sorted(consumed),
                _MAX_CANDIDATES,
                _CONSISTENCY_DISTANCE,
                _CONSISTENCY_DISTANCE,
            ],
        )
        candidates = cursor.fetchall()
    proposals = [
        (Anchor(photo_id, detection_id), set(consistent_photo_ids))
        for photo_id, detection_id, consistent_photo_ids in candidates
    ]
    apparel_neighbors = None
    if proposals and apparel_enabled:
        try:
            apparel_neighbors = continuation_apparel_neighbors(
                root, tuple(anchor for anchor, _ in proposals), model
            )
        except DatabaseError:
            raise ContinuationUnavailable from None

    groups: list[list[Anchor]] = []
    for candidate, neighbors in proposals:
        compatible_apparel = (
            apparel_neighbors.get(candidate.photo_id) if apparel_neighbors is not None else None
        )
        if apparel_neighbors is not None and compatible_apparel is None:
            continue
        for group in groups:
            if all(anchor.photo_id in neighbors for anchor in group):
                if len(group) < _MAX_MEMBERS and (
                    compatible_apparel is None
                    or all(anchor.photo_id in compatible_apparel for anchor in group)
                ):
                    group.append(candidate)
                break
        else:
            if len(groups) < _MAX_CENTROIDS:
                groups.append([candidate])
    return tuple(tuple(group) for group in groups)


def _auto_source(groups: tuple[tuple[Anchor, ...], ...]) -> dict[str, Any]:
    return {
        "kind": _AUTO_SOURCE,
        "centroids": [
            {
                "photo_ids": [anchor.photo_id for anchor in group],
                "detection_ids": [str(anchor.detection_id) for anchor in group],
            }
            for group in groups
        ],
    }


def _negative_anchor_photo_ids(
    root: SelfieSearch, *, paid_watermarked_previews_enabled: bool, automatic: bool
) -> frozenset[str]:
    state = negative_feedback_state(
        root, paid_watermarked_previews_enabled=paid_watermarked_previews_enabled
    )
    if automatic:
        return state.manual_photo_ids | state.similar_photo_ids | state.excluded_photo_ids
    return state.excluded_photo_ids


@transaction.atomic
@sensitive_variables("query", "payload")
def submit_continuation(
    *,
    search: SelfieSearch,
    user,
    photo_id: str | None,
    paid_watermarked_previews_enabled: bool = False,
) -> SelfieSearch:
    root = (
        SelfieSearch.objects.select_for_update(of=("self",))
        .select_related("event")
        .filter(
            pk=search.pk,
            parent_search__isnull=True,
            status=SelfieSearch.Status.READY,
            event__in=Event.objects.site_visible_to(user),
        )
        .first()
    )
    if root is None:
        raise ContinuationUnavailable
    eligible = eligible_saved_result_rows(
        root, paid_watermarked_previews_enabled=paid_watermarked_previews_enabled
    )
    if photo_id is not None and (
        not eligible.filter(photo_id=photo_id).exists()
        or photo_id
        in _negative_anchor_photo_ids(
            root,
            paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
            automatic=False,
        )
    ):
        raise ContinuationUnavailable
    ongoing = root.continuation_passes.filter(
        status__in=(SelfieSearch.Status.QUEUED, SelfieSearch.Status.PROCESSING),
    ).first()
    if ongoing is not None:
        return ongoing

    consumed = consumed_anchor_photo_ids(root, include_automatic=photo_id is None)
    configuration = _gallery_configuration(event=root.event)
    faces = _current_single_faces(
        event=root.event,
        photo_ids=eligible.order_by().values("photo_id"),
        model=configuration["embedding_model"],
    )
    if photo_id is not None:
        if photo_id in consumed:
            raise ContinuationUnavailable
        detection_id = (
            faces.filter(detection__attempt__photo_id=photo_id)
            .values_list("detection_id", flat=True)
            .first()
        )
        if detection_id is None:
            raise ContinuationUnavailable
        configuration["query_source"] = {
            "kind": _MANUAL_SOURCE,
            "photo_id": photo_id,
            "detection_id": str(detection_id),
        }
        try:
            query = _gallery_source_vector(event=root.event, configuration=configuration)
        except GallerySearchUnavailable:
            raise ContinuationUnavailable from None
        payload: tuple[float, ...] | UUID = query
    else:
        eligible = eligible.exclude(
            photo_id__in=_negative_anchor_photo_ids(
                root,
                paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
                automatic=True,
            )
        )
        model = configuration["embedding_model"]
        assert isinstance(model, str)
        groups = _select_centroids(
            faces=faces,
            eligible=eligible,
            consumed=consumed,
            root=root,
            model=model,
            apparel_enabled=is_enabled(APPAREL_SEARCH_EXPANSION, user),
        )
        if not groups:
            raise ContinuationUnavailable
        configuration["query_source"] = _auto_source(groups)
        payload = groups[0][0].detection_id

    configuration[_SHOW_PAID_PREVIEWS_KEY] = paid_watermarked_previews_enabled
    configuration[ANCHOR_EXPANSION_STAFF_ELIGIBILITY_KEY] = is_enabled_for_state(
        FEATURE_FLAG_STAFF, user
    )
    child = SelfieSearch.objects.create(
        parent_search=root,
        event=root.event,
        public_token_digest=_token_digest(secrets.token_urlsafe(32)),
        temporary_object_key="",
        configuration=configuration,
        configuration_hash=_configuration_hash(configuration),
    )
    enqueue_face_search(
        child,
        source=str(FaceSearchTask.Source.GALLERY),
        query=payload,
        now=child.created_at,
    )
    return child


def load_continuation_sources(
    search: SelfieSearch, *, model: str
) -> tuple[tuple[Anchor, ...], ...]:
    """Recheck the selected saved faces immediately before SQL ranking."""
    groups = _configured_centroids(search)
    automatic = bool(groups)
    source = search.configuration.get("query_source")
    if not groups:
        photo_id = _manual_source_photo_id(search)
        detection_id = source.get("detection_id") if isinstance(source, dict) else None
        if photo_id is None or not isinstance(detection_id, str):
            raise RankingError("invalid manual continuation source")
        groups = ((Anchor(photo_id, UUID(detection_id)),),)
    selected = {anchor for group in groups for anchor in group}
    parent = search.parent_search
    if parent is None:
        raise RankingError("continuation has no parent")
    paid_previews_enabled = search.configuration.get(_SHOW_PAID_PREVIEWS_KEY) is True
    photo_ids = (
        eligible_saved_result_rows(
            parent,
            paid_watermarked_previews_enabled=paid_previews_enabled,
        )
        .filter(photo_id__in=[anchor.photo_id for anchor in selected])
        .order_by()
        .values("photo_id")
    )
    photo_ids = photo_ids.exclude(
        photo_id__in=_negative_anchor_photo_ids(
            parent, paid_watermarked_previews_enabled=paid_previews_enabled, automatic=automatic
        )
    )
    current = _current_single_faces(event=search.event, photo_ids=photo_ids, model=model)
    if set(current.values_list("detection__attempt__photo_id", "detection_id")) != {
        (anchor.photo_id, anchor.detection_id) for anchor in selected
    }:
        raise RankingError("continuation source is no longer eligible")
    return groups
