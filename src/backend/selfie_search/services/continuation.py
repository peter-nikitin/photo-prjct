"""Queue one follow-up against a currently visible, unambiguous saved person."""

import secrets
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Exists, F, OuterRef, QuerySet
from django.views.decorators.debug import sensitive_variables
from feature_flags.registry import APPAREL_SEARCH_EXPANSION
from feature_flags.services import is_enabled, is_enabled_for_state
from feature_flags.states import FEATURE_FLAG_STAFF
from picflow.models import Event
from processing.models import FaceEmbeddingVector, PhotoFaceDetection, PhotoProcessingState
from psycopg import Cursor
from search_feedback.services import active_negative_reports

from selfie_search.models import (
    _TERMINAL_SEARCH_STATUSES,
    ANCHOR_EXPANSION_STAFF_ELIGIBILITY_KEY,
    FaceSearchTask,
    SelfieSearch,
    SelfieSearchResult,
)
from selfie_search.services.apparel_expansion import (
    ApparelAnchorVerification,
    current_apparel_sources,
)
from selfie_search.services.face_history import MATCHED_FACE_EVIDENCE_SQL
from selfie_search.services.jobs import enqueue_face_search
from selfie_search.services.negative_feedback import negative_feedback_state
from selfie_search.services.ranking import RankingError
from selfie_search.services.results import (
    eligible_saved_result_rows,
    has_ready_search_pass,
    saved_ready_result_rows,
)
from selfie_search.services.submission import (
    CONTINUATION_PAID_PREVIEWS_KEY,
    GallerySearchUnavailable,
    _configuration_hash,
    _gallery_configuration,
    _gallery_source_vector,
    _token_digest,
    _usable_gallery_vectors,
)

_AUTO_SOURCE = "continuation_centroids"
_MANUAL_SOURCE = "gallery_photo"
_APPAREL_SOURCE = "continuation_apparel"
_MAX_CENTROIDS = 3
_MAX_MEMBERS = 5
_MAX_CANDIDATES = 60
_STRONG_DISTANCE = 0.38
_CONSISTENCY_DISTANCE = 0.35
_APPAREL_FACE_CONFIRMATION_DISTANCE = 0.42


class ContinuationUnavailable(LookupError):
    """The requested result face is unavailable or the search has no unused eligible anchors."""


@dataclass(frozen=True, slots=True)
class Anchor:
    photo_id: str
    detection_id: UUID


def _manual_source_photo_id(search: SelfieSearch) -> str | None:
    source = search.configuration.get("query_source")
    if isinstance(source, dict) and source.get("kind") in (_MANUAL_SOURCE, _APPAREL_SOURCE):
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


def consumed_anchor_photo_ids(search: SelfieSearch) -> set[str]:
    consumed = set()
    root_gallery_seed = _manual_source_photo_id(search)
    if root_gallery_seed is not None:
        consumed.add(root_gallery_seed)
    for child in search.continuation_passes.filter(status=SelfieSearch.Status.READY).only(
        "configuration", "parent_search_id"
    ):
        manual = _manual_source_photo_id(child)
        if manual is not None:
            consumed.add(manual)
        else:
            consumed.update(
                anchor.photo_id for group in _configured_centroids(child) for anchor in group
            )
    return consumed


def _single_gallery_vectors(
    accepted_attempt_ids: tuple[UUID, ...], model: object
) -> QuerySet[FaceEmbeddingVector]:
    second_detection = PhotoFaceDetection.objects.filter(
        attempt_id=OuterRef("detection__attempt_id")
    ).exclude(pk=OuterRef("detection_id"))
    return (
        _usable_gallery_vectors(accepted_attempt_ids=accepted_attempt_ids, model=model)
        .filter(~Exists(second_detection))
        .order_by()
    )


def _saved_face_attempt_ids(*, event_id: int, photo_ids: set[str]) -> tuple[UUID, ...]:
    return tuple(
        PhotoProcessingState.objects.filter(
            photo_id__in=photo_ids,
            processor_type="face_embedding",
            accepted_attempt__event_id=event_id,
            accepted_attempt__photo_id=F("photo_id"),
            accepted_attempt__accepted=True,
        ).values_list("accepted_attempt_id", flat=True)
    )


def _saved_apparel_sources(
    search: SelfieSearch, *, eligible: QuerySet, photo_ids: set[str]
) -> dict[str, int]:
    if not photo_ids:
        return {}
    sources: dict[str, int] = {}
    rows = (
        eligible.filter(
            photo_id__in=photo_ids,
            primary_source=SelfieSearchResult.PrimarySource.APPAREL_EXPANSION,
        )
        .order_by()
        .values_list("photo_id", "search__apparel_evidence__decisions__added_people")
    )
    for photo_id, added_people in rows:
        person_id = added_people.get(photo_id) if isinstance(added_people, dict) else None
        if type(person_id) is int:
            sources[photo_id] = person_id
    return current_apparel_sources(search, sources)


_MANUAL_SAVED_FACES_SQL = """
WITH selected_results AS MATERIALIZED (
    SELECT id, photo_id, search_id, primary_source FROM selfie_search_selfiesearchresult
    WHERE id = ANY(%s::uuid[])
),
first_pass AS MATERIALIZED (
    SELECT first.* FROM ({first_pass_sql}) AS first(id, photo_id, search_id)
    WHERE EXISTS (SELECT 1 FROM selected_results WHERE primary_source = 'apparel_expansion')
),
evidence_rows AS MATERIALIZED (
    SELECT id, photo_id, search_id FROM selected_results
    UNION
    SELECT id, photo_id, search_id FROM first_pass
),
matched_faces AS MATERIALIZED ({matched_face_sql}),
usable_faces(detection_id, photo_id, vector) AS ({usable_sql}),
first_pass_matches AS (
    SELECT (array_agg(DISTINCT matched.detection_id))[1] AS detection_id
    FROM matched_faces matched
    JOIN first_pass first ON first.id = matched.id
    JOIN processing_photoprocessingstate state ON state.photo_id = matched.photo_id
        AND state.processor_type = 'face_embedding'
    JOIN processing_photofacedetection detection ON detection.id = matched.detection_id
        AND detection.attempt_id = state.accepted_attempt_id AND detection.status = 'kept'
    GROUP BY matched.photo_id
    HAVING count(DISTINCT matched.detection_id) = 1
),
first_pass_vectors AS MATERIALIZED (
    SELECT embedding.vector FROM first_pass_matches
    JOIN processing_faceembeddingvector embedding USING (detection_id)
    WHERE embedding.model_version = %s AND vector_norm(embedding.vector) > 0
),
selected_faces AS (
    SELECT matched.photo_id, selected.primary_source,
        (array_agg(DISTINCT matched.detection_id))[1] AS detection_id
    FROM matched_faces matched
    JOIN selected_results selected ON selected.id = matched.id
    JOIN usable_faces usable ON usable.photo_id = matched.photo_id
        AND usable.detection_id = matched.detection_id
    GROUP BY matched.photo_id, selected.primary_source
    HAVING count(DISTINCT matched.detection_id) = 1
)
SELECT selected.photo_id, selected.detection_id
FROM selected_faces selected
JOIN usable_faces usable USING (photo_id, detection_id)
WHERE selected.primary_source <> 'apparel_expansion'
    OR EXISTS (
        SELECT 1 FROM first_pass_vectors first
        WHERE (usable.vector <=> first.vector) <= %s
    )
"""


def _saved_manual_faces(
    search: SelfieSearch,
    *,
    result_ids: Sequence[UUID],
    photo_ids: set[str],
    model: object,
    paid_previews: bool = False,
) -> dict[str, UUID]:
    attempts = _saved_face_attempt_ids(event_id=search.event_id, photo_ids=photo_ids)
    if not attempts:
        return {}
    usable_sql, usable_params = (
        _usable_gallery_vectors(accepted_attempt_ids=attempts, model=model)
        .order_by()
        .values_list("detection_id", "detection__attempt__photo_id", "vector")
        .query.sql_with_params()
    )
    # An apparel result from the initial pass cannot corroborate its own face.
    first_pass_sql, first_pass_params = (
        eligible_saved_result_rows(search, paid_watermarked_previews_enabled=paid_previews)
        .filter(search=search)
        .exclude(primary_source=SelfieSearchResult.PrimarySource.APPAREL_EXPANSION)
        .exclude(photo_id__in=active_negative_reports(search).values("photo_id"))
        .exclude(
            photo_id__in=[
                photo_id
                for photo_id, state in search.negative_feedback_photos.items()
                if state == "excluded"
            ]
        )
        .order_by()
        .values_list("id", "photo_id", "search_id")
        .query.sql_with_params()
    )
    with connection.wrap_database_errors, Cursor(connection.connection) as cursor:
        cursor.execute(
            _MANUAL_SAVED_FACES_SQL.format(
                first_pass_sql=first_pass_sql,
                matched_face_sql=MATCHED_FACE_EVIDENCE_SQL,
                usable_sql=usable_sql,
            ),
            [
                list(result_ids),
                *first_pass_params,
                *usable_params,
                model,
                _APPAREL_FACE_CONFIRMATION_DISTANCE,
            ],
        )
        return dict(cursor.fetchall())


def available_manual_photo_ids(
    search: SelfieSearch,
    *,
    result_rows: Sequence[SelfieSearchResult],
    apparel_enabled: bool = False,
    paid_watermarked_previews_enabled: bool = False,
) -> set[str]:
    """Resolve the person recorded for the exact result row rendered by each card."""
    if not result_rows:
        return set()
    model = _gallery_configuration(event=search.event)["embedding_model"]
    photo_ids = {row.photo_id for row in result_rows}
    result_ids = [row.pk for row in result_rows]
    with_faces = set(
        _saved_manual_faces(
            search,
            result_ids=result_ids,
            photo_ids=photo_ids,
            model=model,
            paid_previews=paid_watermarked_previews_enabled,
        )
    )
    if not apparel_enabled or not (without_faces := photo_ids - with_faces):
        return with_faces
    return (
        with_faces
        | _saved_apparel_sources(
            search,
            eligible=SelfieSearchResult.objects.filter(pk__in=result_ids),
            photo_ids=without_faces,
        ).keys()
    )


# A weak match can join the pool only when two independently trusted saved faces support it.
# The bounded proposals and trust cohorts keep pairwise comparisons at a fixed upper bound.
_ANCHOR_SQL = """
WITH current_faces(photo_id, detection_id, embedding) AS MATERIALIZED (
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
    WHERE result.id = ANY(%s::uuid[])
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
    eligible_result_ids: tuple[UUID, ...],
    consumed: set[str],
    check_apparel: Callable[[tuple[Anchor, ...]], dict[str, set[str]]] | None,
) -> tuple[tuple[Anchor, ...], ...]:
    faces_sql, faces_params = faces.values_list(
        "detection__attempt__photo_id", "detection_id", "vector"
    ).query.sql_with_params()
    with connection.wrap_database_errors, Cursor(connection.connection) as cursor:
        cursor.execute(
            _ANCHOR_SQL.format(faces_sql=faces_sql),
            [
                *faces_params,
                _STRONG_DISTANCE,
                list(eligible_result_ids),
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
    if proposals and check_apparel is not None:
        apparel_neighbors = check_apparel(tuple(anchor for anchor, _ in proposals))

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


def continuation_centroid_source(groups: tuple[tuple[Anchor, ...], ...]) -> dict[str, Any]:
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


def _negative_anchor_photo_ids(root: SelfieSearch, *, automatic: bool) -> frozenset[str]:
    state = negative_feedback_state(root)
    if automatic:
        return state.manual_photo_ids | state.similar_photo_ids | state.excluded_photo_ids
    return state.manual_photo_ids | state.excluded_photo_ids


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
            status__in=_TERMINAL_SEARCH_STATUSES,
            event__in=Event.objects.site_visible_to(user),
        )
        .first()
    )
    if root is None or not has_ready_search_pass(root):
        raise ContinuationUnavailable
    result_id = None
    if photo_id is not None:
        if photo_id in _negative_anchor_photo_ids(root, automatic=False):
            raise ContinuationUnavailable
        result_id = _manual_result_id(
            root, photo_id=photo_id, paid_previews=paid_watermarked_previews_enabled
        )
        if result_id is None:
            raise ContinuationUnavailable
    ongoing = root.continuation_passes.filter(
        status__in=(SelfieSearch.Status.QUEUED, SelfieSearch.Status.PROCESSING),
    ).first()
    if ongoing is not None:
        return ongoing

    configuration = _gallery_configuration(event=root.event)
    payload = None
    if photo_id is not None and result_id is not None:
        detection_id = _saved_manual_faces(
            root,
            result_ids=[result_id],
            photo_ids={photo_id},
            model=configuration["embedding_model"],
            paid_previews=paid_watermarked_previews_enabled,
        ).get(photo_id)
        if detection_id is not None:
            configuration["query_source"] = {
                "kind": _MANUAL_SOURCE,
                "photo_id": photo_id,
                "detection_id": str(detection_id),
            }
            try:
                payload = _gallery_source_vector(event=root.event, configuration=configuration)
            except GallerySearchUnavailable:
                raise ContinuationUnavailable from None
        elif is_enabled(APPAREL_SEARCH_EXPANSION, user):
            candidates = _saved_apparel_sources(
                root,
                eligible=SelfieSearchResult.objects.filter(pk=result_id),
                photo_ids={photo_id},
            )
            if (person_id := candidates.get(photo_id)) is None:
                raise ContinuationUnavailable
            configuration["query_source"] = {
                "kind": _APPAREL_SOURCE,
                "photo_id": photo_id,
                "person_id": person_id,
            }
        else:
            raise ContinuationUnavailable
    else:
        configuration["query_source"] = {"kind": _AUTO_SOURCE}

    configuration[CONTINUATION_PAID_PREVIEWS_KEY] = paid_watermarked_previews_enabled
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


def select_continuation_centroids(
    search: SelfieSearch,
    *,
    model: str,
    check_apparel: Callable[[tuple[Anchor, ...]], dict[str, set[str]]] | None,
) -> tuple[tuple[Anchor, ...], ...]:
    parent = search.parent_search
    if parent is None:
        raise RankingError("continuation has no parent")
    eligible = eligible_saved_result_rows(
        parent,
        paid_watermarked_previews_enabled=(
            search.configuration.get(CONTINUATION_PAID_PREVIEWS_KEY) is True
        ),
    ).exclude(photo_id__in=_negative_anchor_photo_ids(parent, automatic=True))
    # Resolve the saved rows before joining vectors. An inlined subquery can lead PostgreSQL
    # to scan detections across the event and apply the saved-result filter after the join.
    saved_rows = tuple(eligible.order_by().values_list("pk", "photo_id"))
    attempts = _saved_face_attempt_ids(
        event_id=search.event_id, photo_ids={photo_id for _, photo_id in saved_rows}
    )
    if not attempts:
        return ()
    return _select_centroids(
        faces=_single_gallery_vectors(attempts, model),
        eligible_result_ids=tuple(result_id for result_id, _ in saved_rows),
        consumed=consumed_anchor_photo_ids(parent),
        check_apparel=check_apparel,
    )


def _manual_result_id(search: SelfieSearch, *, photo_id: str, paid_previews: bool) -> UUID | None:
    return (
        saved_ready_result_rows(search, paid_watermarked_previews_enabled=paid_previews)
        .filter(photo_id=photo_id)
        .values_list("pk", flat=True)
        .first()
    )


def validate_continuation_source(search: SelfieSearch, *, model: str) -> None:
    """Recheck that the queued face still identifies the displayed person."""
    source = search.configuration.get("query_source")
    photo_id = _manual_source_photo_id(search)
    detection_id = source.get("detection_id") if isinstance(source, dict) else None
    parent = search.parent_search
    if photo_id is None or not isinstance(detection_id, str) or parent is None:
        raise RankingError("invalid manual continuation source")
    if photo_id in _negative_anchor_photo_ids(parent, automatic=False):
        raise RankingError("continuation source is no longer eligible")
    paid_previews = search.configuration.get(CONTINUATION_PAID_PREVIEWS_KEY) is True
    result_id = _manual_result_id(parent, photo_id=photo_id, paid_previews=paid_previews)
    if result_id is None or _saved_manual_faces(
        parent,
        result_ids=[result_id],
        photo_ids={photo_id},
        model=model,
        paid_previews=paid_previews,
    ).get(photo_id) != UUID(detection_id):
        raise RankingError("continuation source is no longer eligible")


def is_apparel_continuation(search: SelfieSearch) -> bool:
    source = search.configuration.get("query_source")
    return (
        search.parent_search_id is not None
        and isinstance(source, dict)
        and (source.get("kind") == _APPAREL_SOURCE)
    )


def verify_apparel_continuation(search: SelfieSearch) -> ApparelAnchorVerification:
    source = search.configuration["query_source"]
    photo_id, person_id = source.get("photo_id"), source.get("person_id")
    parent = search.parent_search
    if not isinstance(photo_id, str) or type(person_id) is not int or parent is None:
        raise ContinuationUnavailable
    if photo_id in _negative_anchor_photo_ids(parent, automatic=False):
        raise ContinuationUnavailable
    result_id = _manual_result_id(
        parent,
        photo_id=photo_id,
        paid_previews=search.configuration.get(CONTINUATION_PAID_PREVIEWS_KEY) is True,
    )
    if (
        result_id is None
        or _saved_apparel_sources(
            parent,
            eligible=SelfieSearchResult.objects.filter(pk=result_id),
            photo_ids={photo_id},
        ).get(photo_id)
        != person_id
    ):
        raise ContinuationUnavailable
    return ApparelAnchorVerification(
        photo_id,
        "verified",
        (
            {
                "photo_id": photo_id,
                "person_id": person_id,
                "detection_id": None,
                "anchor_position": 1,
            },
        ),
        [],
        [],
        False,
    )
