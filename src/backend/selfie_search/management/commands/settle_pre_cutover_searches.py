"""Settle explicitly selected unfinished searches from before a fixed cutover."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Any
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from ingestion.storage import StorageUnavailable

from selfie_search.models import SelfieSearch, SelfieSearchAttempt, SelfieSearchJob
from selfie_search.storage import TemporarySelfieStorage

_NONTERMINAL = (
    SelfieSearch.Status.QUEUED,
    SelfieSearch.Status.PROCESSING,
    SelfieSearch.Status.CLEANUP_PENDING,
)
_MAX_SELECTION = 100


class Command(BaseCommand):
    help = "Dry-run or settle explicit pre-cutover unfinished search IDs in a bounded batch."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--cutoff", required=True)
        parser.add_argument("--search-id", action="append", dest="search_ids", required=True)
        parser.add_argument("--limit", type=int, required=True)
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args: object, **options: object) -> None:  # noqa: ARG002
        cutoff = _cutoff(options["cutoff"])
        ids = _ids(options["search_ids"], options["limit"])
        now = timezone.now()
        before = _inventory(ids, cutoff, now)
        receipt: dict[str, object] = {
            "mode": "execute" if options["execute"] else "dry_run",
            "selected": len(ids),
            **before,
            "terminalized": 0,
            "cleanup_pending": 0,
        }
        if options["execute"]:
            storage = TemporarySelfieStorage()
            terminalized = cleanup_pending = 0
            for search_id in ids:
                outcome = _settle_one(search_id, cutoff=cutoff, storage=storage)
                if outcome == "terminalized":
                    terminalized += 1
                elif outcome == "cleanup_pending":
                    cleanup_pending += 1
            receipt["terminalized"] = terminalized
            receipt["cleanup_pending"] = cleanup_pending
            receipt["after"] = _inventory(ids, cutoff, timezone.now())
        self.stdout.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")))


def _cutoff(raw: object) -> datetime:
    parsed = parse_datetime(raw) if isinstance(raw, str) else None
    if parsed is None or timezone.is_naive(parsed):
        raise CommandError("--cutoff must be a timezone-aware ISO datetime")
    if parsed > timezone.now():
        raise CommandError("--cutoff must not be in the future")
    return parsed


def _ids(raw: object, limit: object) -> list[UUID]:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_SELECTION:
        raise CommandError("--limit must be between 1 and 100")
    if not isinstance(raw, list) or not raw or len(raw) > limit:
        raise CommandError("--search-id count must be between 1 and --limit")
    try:
        ids = [UUID(value) for value in raw]
    except (ValueError, TypeError):
        raise CommandError("--search-id must be a UUID") from None
    if len(set(ids)) != len(ids):
        raise CommandError("--search-id values must be unique")
    return ids


def _inventory(ids: list[UUID], cutoff: datetime, now: datetime) -> dict[str, object]:
    searches = list(
        SelfieSearch.objects.filter(pk__in=ids)
        .select_related("event")
        .prefetch_related("job__attempts", "results")
    )
    search_status: Counter[str] = Counter()
    job_status: Counter[str] = Counter()
    attempt_status: Counter[str] = Counter()
    models: Counter[str] = Counter()
    leases: Counter[str] = Counter()
    eligible = active = evidence = objects = results = 0
    for search in searches:
        search_status[search.status] += 1
        frozen = search.configuration.get("embedding_model")
        models[frozen if frozen in ("sface", "adaface-ir18-webface4m") else "other_or_missing"] += 1
        job = getattr(search, "job", None)
        attempts = list(job.attempts.all()) if job else []
        if job:
            job_status[job.status] += 1
        else:
            job_status["none"] += 1
        for attempt in attempts:
            attempt_status[attempt.status] += 1
            if attempt.status == SelfieSearchAttempt.Status.IN_PROGRESS:
                lease = attempt.lease_expires_at
                leases["active" if lease is not None and lease > now else "expired_or_missing"] += 1
            if attempt.error_code or attempt.error_detail:
                evidence += 1
        if search.failure_code:
            evidence += 1
        objects += bool(search.temporary_object_key)
        count = len(search.results.all())
        results += count
        has_active = any(
            attempt.status == SelfieSearchAttempt.Status.IN_PROGRESS
            and attempt.lease_expires_at is not None
            and attempt.lease_expires_at > now
            for attempt in attempts
        )
        if has_active:
            active += 1
        if (
            search.created_at < cutoff
            and search.status in _NONTERMINAL
            and not has_active
            and not count
        ):
            eligible += 1
    return {
        "found": len(searches),
        "eligible": eligible,
        "skipped_active_lease": active,
        "search_status_before": dict(search_status),
        "job_status": dict(job_status),
        "attempt_status": dict(attempt_status),
        "frozen_model": dict(models),
        "in_progress_leases": dict(leases),
        "failure_evidence_fields_present": evidence,
        "temporary_object_keys_present": objects,
        "result_rows": results,
    }


def _settle_one(search_id: UUID, *, cutoff: datetime, storage: TemporarySelfieStorage) -> str:
    now = timezone.now()
    with transaction.atomic():
        search = SelfieSearch.objects.select_for_update().filter(pk=search_id).first()
        if search is None or search.created_at >= cutoff or search.status not in _NONTERMINAL:
            return "skipped"
        if search.results.exists():
            return "skipped"
        job = SelfieSearchJob.objects.select_for_update().filter(search=search).first()
        attempts = (
            list(SelfieSearchAttempt.objects.select_for_update().filter(job=job)) if job else []
        )
        if any(
            attempt.status == SelfieSearchAttempt.Status.IN_PROGRESS
            and attempt.lease_expires_at is not None
            and attempt.lease_expires_at > now
            for attempt in attempts
        ):
            return "skipped"
        if job and job.status != SelfieSearchJob.Status.FAILED:
            job.status = SelfieSearchJob.Status.FAILED
            job.completed_at = now
            job.save(update_fields=["status", "completed_at"])
        for attempt in attempts:
            if attempt.status == SelfieSearchAttempt.Status.IN_PROGRESS:
                attempt.status = SelfieSearchAttempt.Status.EXPIRED
                attempt.terminal_at = now
                attempt.save(update_fields=["status", "terminal_at"])
        search.status = SelfieSearch.Status.CLEANUP_PENDING
        search.intended_terminal_status = SelfieSearch.Status.FAILED
        search.state_changed_at = now
        search.save(update_fields=["status", "intended_terminal_status", "state_changed_at"])
        key = search.temporary_object_key
    if key:
        try:
            storage.delete(key=key)
        except StorageUnavailable:
            return "cleanup_pending"
    with transaction.atomic():
        search = SelfieSearch.objects.select_for_update().get(pk=search_id)
        if search.status != SelfieSearch.Status.CLEANUP_PENDING:
            return "skipped"
        search.status = SelfieSearch.Status.FAILED
        search.temporary_object_key = ""
        search.cleanup_confirmed_at = timezone.now()
        search.terminal_at = search.cleanup_confirmed_at
        search.state_changed_at = search.cleanup_confirmed_at
        search.matched_photo_count = 0
        search.save(
            update_fields=[
                "status",
                "temporary_object_key",
                "cleanup_confirmed_at",
                "terminal_at",
                "state_changed_at",
                "matched_photo_count",
            ]
        )
    return "terminalized"
