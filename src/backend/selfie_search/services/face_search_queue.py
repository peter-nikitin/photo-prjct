"""Shared PostgreSQL work queue for face matching and result publication."""

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from functools import cache, partial
from time import perf_counter

from config.queue_events import notify_queue_completion
from config.queue_work import MATCHING_QUEUES, schedule_transition
from django.db import DatabaseError, connection, transaction
from django.db.models import Q, Subquery
from django.utils import timezone
from feature_flags.registry import APPAREL_SEARCH_EXPANSION, FACE_SEARCH_ANCHOR_EXPANSION
from feature_flags.services import is_enabled_for_background_task

from selfie_search.models import (
    ANCHOR_EXPANSION_STAFF_ELIGIBILITY_KEY,
    FaceSearchTask,
    SelfieSearch,
    SelfieSearchAttempt,
)
from selfie_search.observability import emit_selfie_observability_failure
from selfie_search.services import apparel_telemetry
from selfie_search.services.anchor_expansion import append_anchor_matches, find_anchor_matches
from selfie_search.services.apparel_expansion import (
    ContinuationApparelChecks,
    check_continuation_apparel,
    expand_apparel,
    verify_apparel_anchors,
)
from selfie_search.services.cluster_expansion import direct_only_ranked_photos
from selfie_search.services.continuation import (
    Anchor,
    ContinuationUnavailable,
    continuation_centroid_source,
    is_apparel_continuation,
    is_automatic_continuation,
    select_continuation_centroids,
    validate_continuation_source,
    verify_apparel_continuation,
)
from selfie_search.services.continuation_ranking import (
    original_gallery_seed,
    previous_matches,
    rank_continuation_centroids,
)
from selfie_search.services.face_history import face_history_exclusions
from selfie_search.services.jobs import (
    emit_ranking_finished,
    expand_direct_ranking,
    prepare_search_result,
)
from selfie_search.services.negative_feedback import refresh_negative_feedback
from selfie_search.services.ranking import AnchorMatch, RankingError, _configuration
from selfie_search.services.read_selection import rank_selected_direct, report_direct_ranking
from selfie_search.services.submission import CONTINUATION_PAID_PREVIEWS_KEY, _configuration_hash

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(seconds=5)
RANKING_TIMEOUT_MS = 120_000
APPAREL_TIMEOUT_MS = 2_000
APPAREL_MAX_ATTEMPTS = 2


class _ApparelSearchUnavailable(Exception):
    pass


def recover_processing() -> int:
    """Requeue interrupted work at startup of the single executor."""
    now = timezone.now()
    with transaction.atomic():
        tasks = FaceSearchTask.objects.filter(status=FaceSearchTask.Status.PROCESSING)
        SelfieSearch.objects.filter(matching_task__in=tasks).update(
            status=SelfieSearch.Status.QUEUED, state_changed_at=now
        )
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH recovered AS (
                    UPDATE selfie_search_facesearchtask
                    SET status = 'queued', available_at = %s, attempt_count = 0
                    WHERE status = 'processing'
                    RETURNING source
                )
                SELECT source, count(*) FROM recovered GROUP BY source
                """,
                [now],
            )
            recovered = cursor.fetchall()
        for source, count in recovered:
            schedule_transition(
                MATCHING_QUEUES.get(source),
                str(FaceSearchTask.Status.PROCESSING),
                str(FaceSearchTask.Status.QUEUED),
                amount=count,
            )
        return sum(count for _, count in recovered)


def recover_missing_payloads() -> None:
    """Fail unfinished searches whose payloads were omitted from a logical copy."""
    while True:
        with transaction.atomic():
            searches = list(
                SelfieSearch.objects.select_for_update(of=("self",))
                .filter(
                    stage=SelfieSearch.Stage.MATCHING,
                    status__in=(SelfieSearch.Status.QUEUED, SelfieSearch.Status.PROCESSING),
                    matching_task__isnull=True,
                )
                .order_by("id")[:100]
            )
            if not searches:
                return
            now = timezone.now()
            for search in searches:
                prepare_search_result(
                    search,
                    intended_status=str(SelfieSearch.Status.FAILED),
                    failure_code="matching_payload_missing",
                    now=now,
                )


def claim() -> FaceSearchTask | None:
    now = timezone.now()
    with transaction.atomic():
        task = (
            FaceSearchTask.objects.select_for_update(of=("self", "search"), skip_locked=True)
            .select_related("search__event")
            .defer("vector")
            .filter(status=FaceSearchTask.Status.QUEUED)
            .filter(Q(available_at__lte=now) | Q(expires_at__lte=now))
            .order_by("available_at", "id")
            .first()
        )
        if task is None:
            return None
        task.status = FaceSearchTask.Status.PROCESSING
        task.attempt_count += 1
        task.save(update_fields=["status", "attempt_count"])
        schedule_transition(
            MATCHING_QUEUES.get(task.source), str(FaceSearchTask.Status.QUEUED), task.status
        )
        search = task.search
        search.status = SelfieSearch.Status.PROCESSING
        search.state_changed_at = now
        if search.matching_started_at is None:
            search.matching_started_at = now
        search.save(update_fields=["status", "state_changed_at", "matching_started_at"])
        return task


def _expired(task: FaceSearchTask, now: datetime) -> bool:
    return task.expires_at is not None and task.expires_at <= now


def _set_statement_timeout(timeout_ms: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('statement_timeout', %s, true)", [str(timeout_ms)])


def process_face_search(task: FaceSearchTask) -> None:
    search = task.search
    if search.status != SelfieSearch.Status.PROCESSING:
        return
    if _expired(task, timezone.now()):
        _fail(task, code="matching_query_expired")
        return
    if task.event_id != search.event_id or task.embedding_model != _configuration(search).model:
        raise RankingError("matching payload does not match the search")

    # Resolve the vector inside PostgreSQL so it is never included in the SQL text or worker logs.
    query = Subquery(FaceSearchTask.objects.filter(pk=task.pk).values("vector"))
    is_continuation = search.parent_search_id is not None
    automatic = is_continuation and is_automatic_continuation(search)
    apparel_only = is_apparel_continuation(search)
    previous = previous_matches(search) if is_continuation else None
    gallery_seed = original_gallery_seed(search) if is_continuation else None

    @cache
    def prior_photos() -> tuple[str, ...]:
        if previous is None:
            return ()
        return tuple(previous.values_list("photo_id", flat=True)) + (
            (gallery_seed,) if gallery_seed is not None else ()
        )

    anchor_matches: tuple[AnchorMatch, ...] = ()
    verification = None
    apparel_spent_ms = 0
    continuation_checks: ContinuationApparelChecks | None = None
    staff_eligible = search.configuration.get(ANCHOR_EXPANSION_STAFF_ELIGIBILITY_KEY) is True
    apparel_enabled = (automatic or apparel_only) and is_enabled_for_background_task(
        APPAREL_SEARCH_EXPANSION, staff_eligible=staff_eligible
    )
    if apparel_only and not apparel_enabled:
        _fail(task, code="continuation_unavailable")
        return
    with transaction.atomic():
        timeout_ms = RANKING_TIMEOUT_MS
        started_at = timezone.now()
        if task.expires_at is not None:
            remaining_ms = int((task.expires_at - started_at).total_seconds() * 1000)
            if remaining_ms <= 0:
                _fail(task, code="matching_query_expired")
                return
            timeout_ms = min(timeout_ms, remaining_ms)
        _set_statement_timeout(timeout_ms)
        sources: tuple[tuple[Anchor, ...], ...] = ()
        if apparel_only:
            ranking = None
        elif automatic:

            def check_apparel(anchors: tuple[Anchor, ...]) -> dict[str, set[str]]:
                nonlocal apparel_spent_ms, continuation_checks
                started = perf_counter()
                elapsed_ms = int((timezone.now() - started_at).total_seconds() * 1000)
                remaining_ms = timeout_ms - elapsed_ms
                if remaining_ms <= 0:
                    apparel_telemetry.record(
                        "continuation_precheck",
                        search.pk,
                        0,
                        APPAREL_TIMEOUT_MS,
                        0,
                        "matching_deadline_exhausted",
                    )
                    raise _ApparelSearchUnavailable
                try:
                    with apparel_telemetry.stage(
                        "continuation_precheck",
                        search.pk,
                        APPAREL_TIMEOUT_MS,
                        min(remaining_ms, APPAREL_TIMEOUT_MS),
                        started=started,
                    ) as timing:
                        _set_statement_timeout(min(remaining_ms, APPAREL_TIMEOUT_MS))
                        continuation_checks = check_continuation_apparel(
                            search, anchors, task.embedding_model
                        )
                except (DatabaseError, ValueError):
                    logger.warning("apparel_expansion_unavailable")
                    raise _ApparelSearchUnavailable from None
                apparel_spent_ms = timing.elapsed_ms
                _set_statement_timeout(max(1, remaining_ms - apparel_spent_ms))
                return continuation_checks.compatible_neighbors()

            sources = select_continuation_centroids(
                search,
                model=task.embedding_model,
                check_apparel=check_apparel if apparel_enabled else None,
            )
            if not sources:
                _fail(task, code="continuation_unavailable")
                return
            ranking = rank_continuation_centroids(search, sources)
            report_direct_ranking(search, ranking)
        else:
            if is_continuation and task.source == FaceSearchTask.Source.GALLERY:
                validate_continuation_source(search, model=task.embedding_model)
            ranking = rank_selected_direct(
                search,
                query,
                exclude_photo_ids=previous,
                original_gallery_seed=gallery_seed,
            )
            apparel_enabled = ranking.has_vectors and is_enabled_for_background_task(
                APPAREL_SEARCH_EXPANSION, staff_eligible=staff_eligible
            )
        if apparel_enabled:
            now = timezone.now()
            if _expired(task, now):
                _fail(task, code="matching_query_expired")
                return
            remaining_ms = timeout_ms - int((now - started_at).total_seconds() * 1000)
            remaining_apparel_ms = min(remaining_ms, APPAREL_TIMEOUT_MS - apparel_spent_ms)
            if remaining_apparel_ms <= 0:
                apparel_telemetry.record(
                    "anchor_verification",
                    search.pk,
                    0,
                    APPAREL_TIMEOUT_MS - apparel_spent_ms,
                    0,
                    "matching_deadline_exhausted" if remaining_ms <= 0 else "budget_exhausted",
                )
                logger.warning("apparel_expansion_unavailable")
                raise _ApparelSearchUnavailable
            verification_started = perf_counter()
            try:
                with apparel_telemetry.stage(
                    "anchor_verification",
                    search.pk,
                    APPAREL_TIMEOUT_MS - apparel_spent_ms,
                    remaining_apparel_ms,
                    started=verification_started,
                ) as timing:
                    _set_statement_timeout(remaining_apparel_ms)
                    if ranking is None:
                        verification = verify_apparel_continuation(search)
                    else:
                        verification = verify_apparel_anchors(
                            search,
                            ranking.photos,
                            embedding_model=task.embedding_model,
                            gallery=task.source == FaceSearchTask.Source.GALLERY and not automatic,
                            continuation_groups=sources if automatic else (),
                            continuation_checks=continuation_checks,
                        )
                    if automatic:
                        accepted = {row["photo_id"] for row in verification.anchors}
                        if any(
                            outcome != "no_compatible_appearance"
                            and any(anchor.photo_id not in accepted for anchor in group)
                            for group, outcome in zip(
                                sources, verification.group_outcomes, strict=True
                            )
                        ):
                            raise ValueError("continuation appearance anchors changed")
            except ContinuationUnavailable:
                _fail(task, code="continuation_unavailable")
                return
            except (DatabaseError, ValueError):
                logger.warning("apparel_expansion_unavailable")
                raise _ApparelSearchUnavailable from None
            apparel_spent_ms += timing.elapsed_ms

        if (
            ranking is not None
            and ranking.photos
            # Multiple prepared centroids already performed this stage in one cohort scan.
            and not automatic
            and is_enabled_for_background_task(
                FACE_SEARCH_ANCHOR_EXPANSION, staff_eligible=staff_eligible
            )
        ):
            source = search.configuration.get("query_source")
            source_photo_id = source.get("photo_id") if isinstance(source, dict) else None
            if task.source != FaceSearchTask.Source.GALLERY or isinstance(source_photo_id, str):
                now = timezone.now()
                if _expired(task, now):
                    _fail(task, code="matching_query_expired")
                    return
                remaining_ms = timeout_ms - int((now - started_at).total_seconds() * 1000)
                if remaining_ms <= 0:
                    _fail(task, code="failed")
                    return
                _set_statement_timeout(remaining_ms)
                anchor_matches = find_anchor_matches(
                    search,
                    ranking.photos,
                    embedding_model=task.embedding_model,
                    source_photo_id=source_photo_id if isinstance(source_photo_id, str) else None,
                    verified_detection_ids=(
                        verification.centroid_detection_ids if verification is not None else None
                    ),
                    prior_photo_ids=prior_photos(),
                )
    expansion = (
        direct_only_ranked_photos((), outcome="disabled")
        if ranking is None
        else expand_direct_ranking(
            search=search, ranked=ranking.photos, query=query, prior_photos=prior_photos
        )
    )
    if anchor_matches:
        expansion = append_anchor_matches(expansion, anchor_matches)
    if verification is not None:
        now = timezone.now()
        if _expired(task, now):
            _fail(task, code="matching_query_expired")
            return
        remaining_ms = timeout_ms - int((now - started_at).total_seconds() * 1000)
        remaining_apparel_ms = min(remaining_ms, APPAREL_TIMEOUT_MS - apparel_spent_ms)
        if remaining_apparel_ms <= 0:
            apparel_telemetry.record(
                "expansion",
                search.pk,
                0,
                APPAREL_TIMEOUT_MS - apparel_spent_ms,
                0,
                "matching_deadline_exhausted" if remaining_ms <= 0 else "budget_exhausted",
            )
            logger.warning("apparel_expansion_unavailable")
            raise _ApparelSearchUnavailable
        try:
            with apparel_telemetry.stage(
                "expansion", search.pk, APPAREL_TIMEOUT_MS - apparel_spent_ms, remaining_apparel_ms
            ):
                with transaction.atomic():
                    _set_statement_timeout(remaining_apparel_ms)
                    expansion = expand_apparel(
                        search,
                        task.pk,
                        expansion,
                        embedding_model=task.embedding_model,
                        verification=verification,
                        continuation_groups=sources if automatic else (),
                        prior_photo_ids=prior_photos(),
                    )
                    if expansion.apparel_evidence is None:
                        raise ValueError("apparel expansion did not supply evidence")
        except (DatabaseError, ValueError):
            logger.warning("apparel_expansion_unavailable")
            raise _ApparelSearchUnavailable from None

    can_publish = ranking is None or ranking.has_vectors
    parent_search = search.parent_search if is_continuation and expansion.results else None
    if parent_search is not None:
        remaining_ms = timeout_ms - int((timezone.now() - started_at).total_seconds() * 1000)
        if remaining_ms <= 0:
            _fail(
                task, code="matching_query_expired" if _expired(task, timezone.now()) else "failed"
            )
            return
        with transaction.atomic():
            _set_statement_timeout(remaining_ms)
            expansion = replace(
                expansion,
                face_history_excluded_photo_ids=face_history_exclusions(
                    parent=parent_search,
                    search=search,
                    expansion=expansion,
                    paid_previews_enabled=(
                        search.configuration.get(CONTINUATION_PAID_PREVIEWS_KEY) is True
                    ),
                ),
            )
    try:
        with transaction.atomic():
            parent = (
                SelfieSearch.objects.select_for_update(of=("self",))
                .select_related("event")
                .get(pk=search.parent_search_id)
                if is_continuation and can_publish and expansion.results
                else None
            )
            locked_search = SelfieSearch.objects.select_for_update().get(pk=task.search_id)
            if locked_search.status != SelfieSearch.Status.PROCESSING:
                _terminal(task)
                return
            if (
                locked_search.event_id != search.event_id
                or locked_search.configuration_hash != search.configuration_hash
                or locked_search.configuration != search.configuration
            ):
                raise RankingError("search configuration changed during matching")
            search = locked_search
            if _expired(task, timezone.now()):
                _fail(task, code="matching_query_expired")
                return
            intended_status = (
                SelfieSearch.Status.READY if can_publish else SelfieSearch.Status.SEARCH_UNAVAILABLE
            )
            if ranking is not None:
                search.eligible_photo_count = ranking.eligible_photo_count
                search.eligible_face_count = ranking.eligible_face_count
            if automatic:
                search.configuration["query_source"] = continuation_centroid_source(sources)
                search.configuration_hash = _configuration_hash(search.configuration)
                search.save(update_fields=["configuration", "configuration_hash"])
            prepare_search_result(
                search, intended_status=str(intended_status), failure_code="", expansion=expansion
            )
            if parent is not None:
                refresh_negative_feedback(parent)
            _terminal(task)
            if intended_status == SelfieSearch.Status.READY:
                transaction.on_commit(
                    partial(_publish_completion, search, task.source), robust=True
                )
    except RankingError:
        raise
    except (DatabaseError, ValueError):
        if not apparel_enabled:
            raise
        logger.warning("apparel_evidence_unavailable")
        raise _ApparelSearchUnavailable from None
    try:
        attempt = (
            SelfieSearchAttempt.objects.filter(
                job__search=search, status=SelfieSearchAttempt.Status.SUCCEEDED
            )
            .only("id")
            .first()
        )
        if attempt is not None and ranking is not None:
            emit_ranking_finished(
                search=search,
                attempt=attempt,
                outcome="succeeded",
                eligible_photo_count=ranking.eligible_photo_count,
                eligible_face_count=ranking.eligible_face_count,
                matched_photo_count=expansion.final_matched_photo_count,
                load_ms=round(ranking.identity_ms + ranking.build_ms),
                rank_ms=round(ranking.ranking_ms),
                expansion=expansion,
                cohort_lookup=ranking,
                reader="pgvector",
                shortlist_count=ranking.shortlist_count,
                retain_expansion_snapshot=ranking.has_vectors,
            )
    except Exception:
        emit_selfie_observability_failure(logger)


def _publish_completion(search: SelfieSearch, source: str) -> None:
    boundaries: tuple[tuple[str, datetime | None], ...]
    if source == FaceSearchTask.Source.GALLERY:
        queue = "gallery_search"
        boundaries = (
            ("matching_wait", search.matching_started_at),
            ("matching_elapsed", search.terminal_at),
        )
    elif source == FaceSearchTask.Source.SELFIE:
        queue = "selfie_search"
        boundaries = (
            ("recognition_wait", search.recognition_started_at),
            ("recognition_elapsed", search.matching_enqueued_at),
            ("matching_wait", search.matching_started_at),
            ("matching_elapsed", search.terminal_at),
        )
    else:
        return

    previous_at = search.created_at
    previous_ms = 0
    segments_ms: dict[str, int] = {}
    for name, boundary in boundaries:
        if boundary is None or boundary < previous_at:
            return
        elapsed_ms = round((boundary - search.created_at).total_seconds() * 1000)
        segments_ms[name] = elapsed_ms - previous_ms
        previous_at, previous_ms = boundary, elapsed_ms
    notify_queue_completion(queue, segments_ms)


def _terminal(task: FaceSearchTask) -> None:
    previous_status = task.status
    task.status = FaceSearchTask.Status.TERMINAL
    task.save(update_fields=["status"])
    schedule_transition(MATCHING_QUEUES.get(task.source), previous_status, task.status)


def _fail(task: FaceSearchTask, *, code: str) -> None:
    with transaction.atomic():
        search = SelfieSearch.objects.select_for_update().get(pk=task.search_id)
        if search.status != SelfieSearch.Status.PROCESSING:
            return
        prepare_search_result(
            search,
            intended_status=str(SelfieSearch.Status.FAILED),
            failure_code=code,
            now=timezone.now(),
        )
        _terminal(task)


def retry(task: FaceSearchTask, *, apparel_failure: bool = False) -> None:
    # Publication might have staged TERMINAL before its transaction was rolled back.
    task.refresh_from_db(fields=["status"])
    now = timezone.now()
    max_attempts = APPAREL_MAX_ATTEMPTS if apparel_failure else MAX_ATTEMPTS
    if _expired(task, now):
        _fail(task, code="matching_query_expired")
    elif task.attempt_count >= max_attempts:
        code = "apparel_search_unavailable" if apparel_failure else "attempts_exhausted"
        _fail(task, code=code)
    else:
        with transaction.atomic():
            if not SelfieSearch.objects.filter(
                pk=task.search_id, status=SelfieSearch.Status.PROCESSING
            ).update(status=SelfieSearch.Status.QUEUED, state_changed_at=now):
                return
            previous_status = task.status
            task.status = FaceSearchTask.Status.QUEUED
            task.available_at = now + RETRY_DELAY
            task.save(update_fields=["status", "available_at"])
            schedule_transition(MATCHING_QUEUES.get(task.source), previous_status, task.status)


def run_once() -> bool:
    task = claim()
    if task is not None:
        try:
            process_face_search(task)
        except _ApparelSearchUnavailable:
            retry(task, apparel_failure=True)
        except DatabaseError as error:
            if getattr(error.__cause__, "sqlstate", None) == "57014":
                code = "matching_query_expired" if _expired(task, timezone.now()) else "failed"
                _fail(task, code=code)
            else:
                retry(task)
        except Exception:
            # Do not expose per-task input or a SQL exception containing query parameters.
            _fail(task, code="failed")

    return task is not None


def prune_terminal_tasks(*, batch_size: int) -> int:
    with transaction.atomic():
        ids = list(
            FaceSearchTask.objects.select_for_update(skip_locked=True)
            .filter(status=FaceSearchTask.Status.TERMINAL)
            .order_by("id")
            .values_list("id", flat=True)[:batch_size]
        )
        deleted, _ = FaceSearchTask.objects.filter(id__in=ids).delete()
        return deleted
