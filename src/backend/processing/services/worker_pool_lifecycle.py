"""Durable fleet admission and host retirement, serialized with existing job leases.

Only register/heartbeat/request_retirement and claim_admission are worker-facing.
The remaining functions are trusted canonical controller seams, never bearer API actions.
Lock order is pool, member, then the existing claim stores. Callbacks keep their old locks.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Any
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone
from selfie_search.models import SelfieSearchAttempt

from processing.models import PhotoProcessingState, ProcessingAttempt, WorkerPool, WorkerPoolMember
from processing.services.worker_pool_state import BULK_IDENTITIES, SELFIE_IDENTITIES, Identity

OBSERVATION_MAX_AGE = timedelta(seconds=90)
HEARTBEAT_MAX_AGE = timedelta(seconds=60)
IDLE_PERIOD = timedelta(minutes=10)
RUNNING = {"RUNNING_ACTUAL", "RUNNING_OUTDATED"}
STOPPED = {"STOPPED", "DELETED"}
CLOUD_STATES = (
    RUNNING
    | STOPPED
    | {
        "CREATING_INSTANCE",
        "UPDATING_INSTANCE",
        "DELETING_INSTANCE",
        "STARTING_INSTANCE",
        "STOPPING_INSTANCE",
        "AWAITING_STARTUP_DURATION",
        "CHECKING_HEALTH",
        "OPENING_TRAFFIC",
        "AWAITING_WARMUP_DURATION",
        "CLOSING_TRAFFIC",
        "PREPARING_RESOURCES",
    }
)
ENVELOPE_FIELDS = {"pool", "instance_id", "boot_id", "worker_build"}


class AdmissionDenied(ValueError):
    """Sanitized unavailable admission; do not expose membership or credential evidence."""


class RegistrationChanged(AdmissionDenied):
    """This process must register again; existing attempt callbacks remain valid."""


@dataclass(frozen=True)
class MemberIdentity:
    pool: str
    instance_id: str
    boot_id: UUID
    worker_build: str
    registration_generation: UUID | None = None

    @classmethod
    def parse(cls, data: dict[str, Any]) -> MemberIdentity:
        try:
            identity = cls(
                data["pool"],
                data["instance_id"],
                UUID(data["boot_id"]),
                data["worker_build"],
                UUID(data["registration_generation"])
                if "registration_generation" in data
                else None,
            )
            identity.validate()
            return identity
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AdmissionDenied() from None

    def validate(self) -> None:
        if (
            self.pool not in {"bulk", "selfie"}
            or not _identifier(self.instance_id)
            or not _build(self.worker_build)
            or not isinstance(self.boot_id, UUID)
        ):
            raise AdmissionDenied()


def _identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value) is not None
    )


def _build(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _enabled() -> None:
    if not settings.PHOTO_WORKER_POOL_COORDINATOR_ENABLED:
        raise AdmissionDenied()


def _pool(name: str) -> WorkerPool:
    _enabled()
    try:
        return WorkerPool.objects.select_for_update().get(name=name)
    except WorkerPool.DoesNotExist:
        raise AdmissionDenied() from None


def _member(
    pool: WorkerPool, identity: MemberIdentity, *, process: bool = False
) -> WorkerPoolMember:
    identity.validate()
    try:
        member = WorkerPoolMember.objects.select_for_update().get(
            pool=pool, instance_id=identity.instance_id
        )
    except WorkerPoolMember.DoesNotExist:
        raise AdmissionDenied() from None
    if member.boot_id != identity.boot_id or member.worker_build != identity.worker_build:
        raise AdmissionDenied()
    if process and (
        identity.registration_generation is None
        or identity.registration_generation != member.registration_generation
    ):
        raise RegistrationChanged()
    return member


def _fresh(timestamp: datetime | None, now: datetime, age: timedelta) -> bool:
    return timestamp is not None and now - age <= timestamp <= now


def _cloud_fresh(pool: WorkerPool, now: datetime) -> bool:
    return bool(
        pool.group_id
        and _build(pool.active_build)
        and pool.observation_sequence
        and _fresh(pool.observation_completed_at, now, OBSERVATION_MAX_AGE)
    )


def _observed(pool: WorkerPool, instance_id: str) -> dict[str, str] | None:
    return next((row for row in pool.observed_members if row["instance_id"] == instance_id), None)


def _running(pool: WorkerPool, member: WorkerPoolMember, now: datetime) -> bool:
    row = _observed(pool, member.instance_id)
    return bool(
        _cloud_fresh(pool, now)
        and row
        and row["status"] in RUNNING
        and row["worker_build"] == member.worker_build
    )


def _serving(pool: WorkerPool, member: WorkerPoolMember, now: datetime) -> bool:
    return bool(
        not pool.claims_paused
        and member.ready
        and not member.draining
        and member.retirement_grant is None
        and member.worker_build == pool.active_build
        and _fresh(member.heartbeat_at, now, HEARTBEAT_MAX_AGE)
        and _running(pool, member, now)
    )


def _live_attempts(name: str, now: datetime) -> QuerySet[Any]:
    if name == "selfie":
        return SelfieSearchAttempt.objects.filter(
            status="in_progress", job__status="processing", job__search__status="processing"
        )
    identities = Q()
    for contract, processor, version in BULK_IDENTITIES:
        identities |= Q(
            contract_version=contract, processor_type=processor, processor_version=version
        )
    current = PhotoProcessingState.objects.filter(
        current_attempt_id=OuterRef("pk"),
        current_job_id=OuterRef("job_id"),
        photo_id=OuterRef("photo_id"),
        processor_type=OuterRef("processor_type"),
    )
    return ProcessingAttempt.objects.filter(identities, Exists(current), status="in_progress")


def _has_live(member: WorkerPoolMember, now: datetime) -> bool:
    attempt_id = member.active_processing_attempt_id or member.active_selfie_attempt_id
    if attempt_id is None:
        return False
    # A heartbeat may have sampled time before waiting on a row lock. Keep ownership
    # until existing recovery durably terminates the attempt, not merely clock expiry.
    if _live_attempts(member.pool.name, now).filter(pk=attempt_id).exists():
        return True
    member.active_processing_attempt_id = None
    member.active_selfie_attempt_id = None
    member.idle_since = None
    member.save(update_fields=["active_processing_attempt", "active_selfie_attempt", "idle_since"])
    return False


def _idle(pool: WorkerPool, member: WorkerPoolMember, now: datetime) -> None:
    if _has_live(member, now) or not _serving(pool, member, now):
        member.idle_since = None
    elif member.idle_since is None:
        member.idle_since = now


@transaction.atomic
def configure_pool(name: str, *, group_id: str, active_build: str) -> WorkerPool:
    _enabled()
    if name not in {"bulk", "selfie"} or not _identifier(group_id) or not _build(active_build):
        raise AdmissionDenied()
    pool, created = WorkerPool.objects.select_for_update().get_or_create(
        name=name, defaults={"group_id": group_id, "active_build": active_build}
    )
    if not created and (pool.group_id != group_id or pool.active_build != active_build):
        raise AdmissionDenied()
    return pool


@transaction.atomic
def reactivation_eligible(predecessors: dict[str, dict[str, str]] | None) -> bool:
    """Read current canonical authority for an exact, locally rolled-back predecessor."""
    _enabled()
    pools = {pool.name: pool for pool in WorkerPool.objects.select_for_update().order_by("name")}
    if predecessors is None:
        if pools:
            raise AdmissionDenied()
        return True
    if not isinstance(predecessors, dict) or set(predecessors) != {"bulk", "selfie"}:
        raise AdmissionDenied()
    if set(pools) != set(predecessors):
        raise AdmissionDenied()
    for name, expected in predecessors.items():
        if (
            not isinstance(expected, dict)
            or set(expected) != {"group_id", "active_build"}
            or not _identifier(expected["group_id"])
            or not _build(expected["active_build"])
        ):
            raise AdmissionDenied()
        pool = pools[name]
        if (
            pool.group_id != expected["group_id"]
            or pool.active_build != expected["active_build"]
            or pool.staged_build is not None
            or not pool.claims_paused
            or pool.local_claims_paused
            or pool.members.select_for_update().exists()
            or _live_attempts(name, timezone.now()).exists()
        ):
            raise AdmissionDenied()
    return True


@transaction.atomic
def rebind_pool(
    name: str, *, old_group_id: str, old_build: str, group_id: str, active_build: str
) -> WorkerPool:
    """Move only a cleaned pool's infrastructure identity, leaving product data intact."""
    if (
        name not in {"bulk", "selfie"}
        or not all(
            (
                _identifier(old_group_id),
                _build(old_build),
                _identifier(group_id),
                _build(active_build),
            )
        )
        or old_group_id == group_id
    ):
        raise AdmissionDenied()
    pool = _pool(name)
    if pool.group_id == group_id and pool.active_build == active_build:
        return pool
    if pool.group_id != old_group_id or pool.active_build != old_build:
        raise AdmissionDenied()
    if (
        pool.staged_build is not None
        or not pool.claims_paused
        or pool.local_claims_paused
        or pool.members.select_for_update().exists()
        or _live_attempts(name, timezone.now()).exists()
    ):
        raise AdmissionDenied()
    pool.group_id = group_id
    pool.active_build = active_build
    pool.observation_sequence = 0
    pool.observation_started_at = None
    pool.observation_completed_at = None
    pool.target_size = 0
    pool.observed_members = []
    pool.queue_observed_at = None
    pool.endpoint_available = False
    pool.save()
    return pool


@transaction.atomic
def record_cloud_snapshot(
    name: str,
    *,
    group_id: str,
    sequence: int,
    started_at: datetime,
    completed_at: datetime,
    target_size: int,
    members: list[dict[str, str]],
    complete: bool,
) -> bool:
    """Publish only complete ordered canonical observations with actual per-instance builds."""
    pool = _pool(name)
    now = timezone.now()
    if (
        type(sequence) is not int
        or sequence <= pool.observation_sequence
        or group_id != pool.group_id
    ):
        return False
    valid = (
        complete is True
        and type(target_size) is int
        and 0 <= target_size <= 2
        and isinstance(members, list)
        and len(members) <= 4
    )
    valid = (
        valid
        and timezone.is_aware(started_at)
        and timezone.is_aware(completed_at)
        and started_at <= completed_at <= now
    )
    valid = valid and all(
        isinstance(row, dict)
        and set(row) == {"instance_id", "status", "worker_build"}
        and row["status"] in CLOUD_STATES
        and (
            _identifier(row["instance_id"])
            or (row["instance_id"] == "" and row["status"] not in RUNNING | STOPPED)
        )
        and (_build(row["worker_build"]) or row["worker_build"] == "")
        for row in members
    )
    valid = valid and sum(row["status"] != "DELETED" for row in members) <= 2
    ids = [row["instance_id"] for row in members if row.get("instance_id")] if valid else []
    valid = valid and len(set(ids)) == len(ids)
    if not valid:
        pool.observation_completed_at = None
        pool.save(update_fields=["observation_completed_at"])
        return False
    if pool.observation_started_at is not None and started_at < pool.observation_started_at:
        return False
    pool.observation_sequence = sequence
    pool.observation_started_at = started_at
    pool.observation_completed_at = completed_at
    pool.target_size = target_size
    pool.observed_members = members
    pool.save()
    for member in pool.members.select_for_update().filter(
        retirement_grant__isnull=False, reconciled_at__isnull=True
    ):
        row = _observed(pool, member.instance_id)
        unidentified = any(not item["instance_id"] for item in members)
        if (
            member.granted_at is not None
            and started_at > member.granted_at
            and (
                (row is None and not unidentified) or (row is not None and row["status"] in STOPPED)
            )
        ):
            member.reconciled_at = completed_at
            member.save(update_fields=["reconciled_at"])
    return True


@transaction.atomic
def record_queue_observation(name: str, *, observed_at: datetime, endpoint_available: bool) -> bool:
    pool = _pool(name)
    if not _fresh(observed_at, timezone.now(), OBSERVATION_MAX_AGE) or (
        pool.queue_observed_at is not None and observed_at < pool.queue_observed_at
    ):
        return False
    pool.queue_observed_at = observed_at
    pool.endpoint_available = endpoint_available is True
    pool.save(update_fields=["queue_observed_at", "endpoint_available"])
    return True


@transaction.atomic
def register(identity: MemberIdentity) -> dict[str, object]:
    identity.validate()
    pool = _pool(identity.pool)
    now = timezone.now()
    row = _observed(pool, identity.instance_id)
    if (
        not _cloud_fresh(pool, now)
        or not row
        or row["status"] not in RUNNING
        or row["worker_build"] != identity.worker_build
        or identity.worker_build not in {pool.active_build, pool.staged_build}
    ):
        raise AdmissionDenied()
    member = (
        WorkerPoolMember.objects.select_for_update()
        .filter(instance_id=identity.instance_id)
        .first()
    )
    if member is None:
        member = WorkerPoolMember.objects.create(
            pool=pool,
            instance_id=identity.instance_id,
            boot_id=identity.boot_id,
            worker_build=identity.worker_build,
        )
    elif member.pool_id != pool.pk:
        raise AdmissionDenied()
    elif member.boot_id != identity.boot_id:
        if member.retirement_grant is not None and member.reconciled_at is None:
            raise AdmissionDenied()
        # Keep any old live lease reference: a new process/boot is not a lease reset.
        member.boot_id = identity.boot_id
        member.worker_build = identity.worker_build
        member.ready = member.draining = False
        member.heartbeat_at = member.idle_since = None
        member.retirement_grant = member.granted_at = member.grant_observation_sequence = (
            member.reconciled_at
        ) = None
        member.save()
    elif (
        member.worker_build != identity.worker_build
        or member.retirement_grant is not None
        or member.draining
    ):
        raise AdmissionDenied()
    member.registration_generation = uuid4()
    member.ready = False
    member.heartbeat_at = None
    member.idle_since = None
    member.save(update_fields=["registration_generation", "ready", "heartbeat_at", "idle_since"])
    return {
        "registered": True,
        "draining": member.draining,
        "registration_generation": str(member.registration_generation),
    }


@transaction.atomic
def heartbeat(identity: MemberIdentity, *, ready: bool, draining: bool) -> dict[str, object]:
    pool = _pool(identity.pool)
    member = _member(pool, identity, process=True)
    now = timezone.now()
    member.heartbeat_at = now
    member.draining = member.draining or draining or member.retirement_grant is not None
    member.ready = bool(
        ready
        and not member.draining
        and _running(pool, member, now)
        and member.worker_build in {pool.active_build, pool.staged_build}
    )
    _idle(pool, member, now)
    member.save(update_fields=["heartbeat_at", "draining", "ready", "idle_since"])
    return {"ready": member.ready, "draining": member.draining}


@dataclass
class ClaimAdmission:
    allowed: bool
    member: WorkerPoolMember | None = None

    def bind(self, attempt: ProcessingAttempt | SelfieSearchAttempt) -> None:
        if not self.allowed:
            raise AdmissionDenied()
        if self.member is None:
            return
        if isinstance(attempt, ProcessingAttempt) and self.member.pool.name == "bulk":
            self.member.active_processing_attempt = attempt
        elif isinstance(attempt, SelfieSearchAttempt) and self.member.pool.name == "selfie":
            self.member.active_selfie_attempt = attempt
        else:
            raise AdmissionDenied()
        self.member.idle_since = None
        self.member.save(
            update_fields=["active_processing_attempt", "active_selfie_attempt", "idle_since"]
        )


@contextmanager
def claim_admission(identity: Identity, remote: MemberIdentity | None) -> Iterator[ClaimAdmission]:
    if remote is None and not settings.PHOTO_WORKER_POOL_COORDINATOR_ENABLED:
        yield ClaimAdmission(True)
        return
    name = (
        "bulk"
        if identity in BULK_IDENTITIES
        else "selfie"
        if identity in SELFIE_IDENTITIES
        else None
    )
    if name is None or (remote is not None and remote.pool != name):
        raise AdmissionDenied()
    with transaction.atomic():
        pool = _pool(name)
        now = timezone.now()
        member = _member(pool, remote, process=True) if remote else None
        limit = 2 if name == "bulk" else settings.PHOTO_WORKER_SELFIE_CLAIM_LIMIT
        allowed = (
            type(limit) is int and limit in {1, 2} and _live_attempts(name, now).count() < limit
        )
        if member:
            allowed = allowed and _serving(pool, member, now) and not _has_live(member, now)
            _idle(pool, member, now)
            member.save(update_fields=["idle_since"])
        else:
            allowed = allowed and not pool.local_claims_paused
        yield ClaimAdmission(allowed, member)


def _grant(member: WorkerPoolMember) -> dict[str, str]:
    return {
        "instance_id": member.instance_id,
        "boot_id": str(member.boot_id),
        "grant_id": str(member.retirement_grant),
    }


def _reserve(
    pool: WorkerPool, member: WorkerPoolMember, now: datetime, *, release: bool
) -> dict[str, str] | None:
    if (
        not _cloud_fresh(pool, now)
        or not _fresh(pool.queue_observed_at, now, OBSERVATION_MAX_AGE)
        or not pool.endpoint_available
        or _has_live(member, now)
    ):
        return None
    members = list(pool.members.select_for_update())
    outstanding = [
        other
        for other in members
        if other.retirement_grant is not None and other.reconciled_at is None
    ]
    # One irreversible permission at a time, including release retries and cloud replacement.
    if outstanding or any(row["status"] not in RUNNING | STOPPED for row in pool.observed_members):
        return None
    survivors = sum(other.pk != member.pk and _serving(pool, other, now) for other in members)
    floor = 1 if pool.name == "selfie" else 0
    if survivors < floor or not _running(pool, member, now):
        return None
    if not release:
        actual = sum(row["status"] in RUNNING for row in pool.observed_members)
        if (
            pool.staged_build is not None
            or not _serving(pool, member, now)
            or actual <= pool.target_size
            or survivors < max(floor, pool.target_size)
            or member.idle_since is None
            or now - member.idle_since < IDLE_PERIOD
        ):
            return None
    member.retirement_grant = uuid4()
    member.granted_at = now
    member.grant_observation_sequence = pool.observation_sequence
    member.ready = False
    member.draining = True
    member.idle_since = None
    member.save()
    return _grant(member)


@transaction.atomic
def request_retirement(identity: MemberIdentity) -> dict[str, str] | None:
    pool = _pool(identity.pool)
    member = _member(pool, identity)
    if member.retirement_grant is not None:
        return _grant(member)
    now = timezone.now()
    _idle(pool, member, now)
    member.save(update_fields=["idle_since"])
    return _reserve(pool, member, now, release=False)


@transaction.atomic
def set_claims_paused(name: str, *, paused: bool, local: bool = False) -> None:
    pool = _pool(name)
    field = "local_claims_paused" if local else "claims_paused"
    setattr(pool, field, paused)
    pool.save(update_fields=[field])
    if not local:
        pool.members.update(idle_since=None)


@transaction.atomic
def local_live_leases(name: str) -> int:
    pool = _pool(name)
    field = "active_processing_attempt_id" if name == "bulk" else "active_selfie_attempt_id"
    bound = pool.members.exclude(**{field: None}).values_list(field, flat=True)
    return _live_attempts(name, timezone.now()).exclude(pk__in=bound).count()


def wait_for_local_drain(
    name: str, *, timeout_seconds: float = 900, poll_seconds: float = 1
) -> bool:
    """Return stop permission only after paused local ownership is empty before deadline.

    Recovery is the existing bounded lease recovery, outside the pool lock. Failure or timeout
    must abort the caller's container stop; this seam itself never stops any process.
    """
    if (
        not isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 900
        or not isfinite(poll_seconds)
        or not 0 < poll_seconds <= 30
    ):
        raise ValueError("invalid drain deadline")
    deadline = time.monotonic() + timeout_seconds
    while True:
        with transaction.atomic():
            pool = _pool(name)
            if not pool.local_claims_paused:
                raise AdmissionDenied()
            if local_live_leases(name) == 0:
                return time.monotonic() <= deadline
        if time.monotonic() >= deadline:
            return False
        if name == "selfie":
            from selfie_search.services.jobs import recover_expired_search_attempts
            from selfie_search.storage import TemporarySelfieStorage

            recover_expired_search_attempts(storage=TemporarySelfieStorage(), limit=2)
        else:
            from processing.services.jobs import recover_expired_attempts

            recover_expired_attempts(limit=2)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_seconds, remaining))


@transaction.atomic
def stage_build(name: str, *, active_build: str, staged_build: str) -> None:
    pool = _pool(name)
    if (
        pool.active_build != active_build
        or not _build(staged_build)
        or staged_build == active_build
        or pool.staged_build not in {None, staged_build}
    ):
        raise AdmissionDenied()
    pool.staged_build = staged_build
    pool.save(update_fields=["staged_build"])


@transaction.atomic
def cancel_staged_build(name: str, *, active_build: str, staged_build: str) -> None:
    """Cancel before promotion only after any candidate retirement is reconciled.

    Retire/drain the candidate first, observe its stop, then clear the staged release. Never
    makes a drained boot available. A never-created candidate still needs a complete snapshot.
    """
    pool = _pool(name)
    now = timezone.now()
    if (
        pool.active_build != active_build
        or pool.staged_build != staged_build
        or not _cloud_fresh(pool, now)
    ):
        raise AdmissionDenied()
    if any(
        row["status"] not in RUNNING | STOPPED
        or (row["status"] in RUNNING and row["worker_build"] == staged_build)
        for row in pool.observed_members
    ):
        raise AdmissionDenied()
    members = list(pool.members.select_for_update())
    if any(
        (member.retirement_grant is not None and member.reconciled_at is None)
        or (member.worker_build == staged_build and _has_live(member, now))
        for member in members
    ):
        raise AdmissionDenied()
    if sum(_serving(pool, member, now) for member in members) < (1 if name == "selfie" else 0):
        raise AdmissionDenied()
    pool.staged_build = None
    pool.save(update_fields=["staged_build"])


@transaction.atomic
def promote_build(name: str, *, active_build: str, staged_build: str) -> None:
    pool = _pool(name)
    if pool.active_build != active_build or pool.staged_build != staged_build:
        raise AdmissionDenied()
    now = timezone.now()
    candidates = [
        member
        for member in pool.members.select_for_update()
        if member.worker_build == staged_build
        and member.ready
        and not member.draining
        and member.retirement_grant is None
        and _fresh(member.heartbeat_at, now, HEARTBEAT_MAX_AGE)
        and _running(pool, member, now)
    ]
    if not candidates:
        raise AdmissionDenied()
    pool.active_build = staged_build
    pool.staged_build = None
    pool.save(update_fields=["active_build", "staged_build"])
    pool.members.update(idle_since=None)


@transaction.atomic
def reserve_release_retirement(
    identity: MemberIdentity, *, active_build: str, staged_build: str | None
) -> dict[str, str] | None:
    pool = _pool(identity.pool)
    member = _member(pool, identity)
    if member.retirement_grant is not None:
        return _grant(member)
    if pool.active_build != active_build or pool.staged_build != staged_build:
        raise AdmissionDenied()
    now = timezone.now()
    others = list(pool.members.select_for_update().exclude(pk=member.pk))
    if any(other.retirement_grant is not None and other.reconciled_at is None for other in others):
        return None
    floor = 1 if pool.name == "selfie" else 0
    if sum(_serving(pool, other, now) for other in others) < floor or not _running(
        pool, member, now
    ):
        return None
    member.draining = True
    member.ready = False
    member.idle_since = None
    member.save(update_fields=["draining", "ready", "idle_since"])
    return _reserve(pool, member, now, release=True)
