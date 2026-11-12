"""Shared PostgreSQL work queue for face matching and result publication."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from django.db import DatabaseError, connection, transaction
from django.db.models import Q, Subquery
from django.utils import timezone

from selfie_search.models import FaceSearchTask, SelfieSearch, SelfieSearchAttempt
from selfie_search.observability import emit_selfie_observability_failure
from selfie_search.services.jobs import (
    CleanupPending,
    _CleanupStorage,
    confirm_search_cleanup,
    emit_ranking_finished,
    expand_direct_ranking,
    prepare_search_result,
)
from selfie_search.services.ranking import RankingError, _configuration
from selfie_search.services.read_selection import rank_selected_direct
from selfie_search.storage import TemporarySelfieStorage

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(seconds=5)
CLEANUP_RETRY_DELAY = timedelta(seconds=30)
RANKING_TIMEOUT_MS = 120_000


def recover_processing() -> int:
    """Requeue interrupted work at startup of the single executor."""
    now = timezone.now()
    with transaction.atomic():
        tasks = FaceSearchTask.objects.filter(status=FaceSearchTask.Status.PROCESSING)
        SelfieSearch.objects.filter(matching_task__in=tasks).update(
            status=SelfieSearch.Status.QUEUED, state_changed_at=now
        )
        return tasks.update(status=FaceSearchTask.Status.QUEUED, available_at=now, attempt_count=0)


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
        search = task.search
        search.status = SelfieSearch.Status.PROCESSING
        search.state_changed_at = now
        if search.matching_started_at is None:
            search.matching_started_at = now
        search.save(update_fields=["status", "state_changed_at", "matching_started_at"])
        return task


def _expired(task: FaceSearchTask, now: datetime) -> bool:
    return task.expires_at is not None and task.expires_at <= now


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
    with transaction.atomic():
        timeout_ms = RANKING_TIMEOUT_MS
        if task.expires_at is not None:
            remaining_ms = int((task.expires_at - timezone.now()).total_seconds() * 1000)
            if remaining_ms <= 0:
                _fail(task, code="matching_query_expired")
                return
            timeout_ms = min(timeout_ms, remaining_ms)
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('statement_timeout', %s, true)", [str(timeout_ms)])
        ranking = rank_selected_direct(search, query)
    expansion = expand_direct_ranking(search=search, ranked=ranking.photos, query=query)

    with transaction.atomic():
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
        now = timezone.now()
        if _expired(task, now):
            _fail(task, code="matching_query_expired")
            return
        intended_status = (
            SelfieSearch.Status.READY
            if ranking.has_vectors
            else SelfieSearch.Status.SEARCH_UNAVAILABLE
        )
        search.eligible_photo_count = ranking.eligible_photo_count
        search.eligible_face_count = ranking.eligible_face_count
        prepare_search_result(
            search,
            intended_status=str(intended_status),
            failure_code="",
            expansion=expansion,
        )
        _terminal(task)
    try:
        attempt = (
            SelfieSearchAttempt.objects.filter(
                job__search=search, status=SelfieSearchAttempt.Status.SUCCEEDED
            )
            .only("id")
            .first()
        )
        if attempt is not None:
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


def _terminal(task: FaceSearchTask) -> None:
    task.status = FaceSearchTask.Status.TERMINAL
    task.save(update_fields=["status"])


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


def retry(task: FaceSearchTask) -> None:
    now = timezone.now()
    if _expired(task, now):
        _fail(task, code="matching_query_expired")
    elif task.attempt_count >= MAX_ATTEMPTS:
        _fail(task, code="attempts_exhausted")
    else:
        with transaction.atomic():
            SelfieSearch.objects.filter(pk=task.search_id).update(
                status=SelfieSearch.Status.QUEUED, state_changed_at=now
            )
            task.status = FaceSearchTask.Status.QUEUED
            task.available_at = now + RETRY_DELAY
            task.save(update_fields=["status", "available_at"])


def run_once(
    *,
    storage: _CleanupStorage | None = None,
    start_cleanup_watchdog: Callable[[], None] | None = None,
) -> bool:
    task = claim()
    if task is not None:
        try:
            process_face_search(task)
        except DatabaseError as error:
            if getattr(error.__cause__, "sqlstate", None) == "57014":
                code = "matching_query_expired" if _expired(task, timezone.now()) else "failed"
                _fail(task, code=code)
            else:
                retry(task)
        except Exception:
            # Do not expose per-task input or a SQL exception containing query parameters.
            _fail(task, code="failed")

    if start_cleanup_watchdog is not None:
        start_cleanup_watchdog()
    now = timezone.now()
    pending = SelfieSearch.objects.filter(
        stage=SelfieSearch.Stage.MATCHING, status=SelfieSearch.Status.CLEANUP_PENDING
    )
    ready_pending = pending.filter(state_changed_at__lte=now - CLEANUP_RETRY_DELAY)
    if task is not None:
        ready_pending = pending.filter(
            Q(pk=task.search_id) | Q(state_changed_at__lte=now - CLEANUP_RETRY_DELAY)
        )
    pending_search_id: UUID | None = (
        ready_pending.order_by("state_changed_at", "id").values_list("id", flat=True).first()
    )
    if pending_search_id is None:
        return task is not None
    # Reserve the next retry before I/O so a watchdog exit also defers this cleanup.
    pending.filter(pk=pending_search_id).update(state_changed_at=timezone.now())
    try:
        confirm_search_cleanup(
            search_id=pending_search_id,
            storage=storage or TemporarySelfieStorage.for_matching_cleanup(),
        )
    except CleanupPending:
        return task is not None
    return True


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
