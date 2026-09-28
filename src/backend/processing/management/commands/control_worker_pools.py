"""Canonical process-only coordination interface; deliberately no HTTP equivalent."""

import json
import sys
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, transaction
from django.utils import timezone

from processing.models import WorkerPool
from processing.services import worker_pool_lifecycle as lifecycle


@transaction.atomic
def status() -> dict[str, Any]:
    now = timezone.now()
    result = {}
    for pool in WorkerPool.objects.select_for_update().order_by("name"):
        members = []
        for member in pool.members.select_for_update().order_by("instance_id"):
            members.append(
                {
                    "instance_id": member.instance_id,
                    "boot_id": str(member.boot_id),
                    "worker_build": member.worker_build,
                    "registration_generation": str(member.registration_generation),
                    "warm": bool(
                        member.ready
                        and not member.draining
                        and member.retirement_grant is None
                        and lifecycle._fresh(member.heartbeat_at, now, lifecycle.HEARTBEAT_MAX_AGE)
                        and lifecycle._running(pool, member, now)
                    ),
                    "serving": lifecycle._serving(pool, member, now),
                    "draining": member.draining,
                    "grant": str(member.retirement_grant) if member.retirement_grant else None,
                    "reconciled": member.reconciled_at is not None,
                }
            )
        result[pool.name] = {
            "group_id": pool.group_id,
            "active_build": pool.active_build,
            "staged_build": pool.staged_build,
            "claims_paused": pool.claims_paused,
            "local_claims_paused": pool.local_claims_paused,
            "fresh": bool(
                lifecycle._cloud_fresh(pool, now)
                and pool.endpoint_available
                and lifecycle._fresh(pool.queue_observed_at, now, lifecycle.OBSERVATION_MAX_AGE)
            ),
            "observed_members": pool.observed_members,
            "target_size": pool.target_size,
            "live_attempts": lifecycle._live_attempts(pool.name, now).count(),
            "local_live_attempts": lifecycle.local_live_leases(pool.name),
            "members": members,
        }
    return result


def recover(name: str) -> None:
    # Reuse the existing durable recovery authority and its bounded batches.
    if name == "selfie":
        from selfie_search.services.jobs import recover_expired_search_attempts
        from selfie_search.storage import TemporarySelfieStorage

        recover_expired_search_attempts(storage=TemporarySelfieStorage(), limit=2)
    elif name == "bulk":
        from processing.services.jobs import recover_expired_attempts

        recover_expired_attempts(limit=2)
    else:
        raise ValueError("invalid pool")


def execute(request: dict[str, Any]) -> dict[str, Any]:
    request = request.copy()
    operation = request.pop("operation")
    if operation == "status" and not request:
        return status()
    fields = {
        "configure": {"pool", "group_id", "active_build"},
        "pause": {"pool", "paused", "local"},
        "drain-local": {"pool", "timeout_seconds"},
        "stage": {"pool", "active_build", "staged_build"},
        "promote": {"pool", "active_build", "staged_build"},
        "cancel": {"pool", "active_build", "staged_build"},
        "retire": {"identity", "active_build", "staged_build"},
        "recover": {"pool"},
    }
    if operation not in fields or set(request) != fields[operation]:
        raise ValueError("invalid control request")
    if operation == "retire":
        identity = lifecycle.MemberIdentity.parse(request.pop("identity"))
        return {"grant": lifecycle.reserve_release_retirement(identity, **request)}
    name = request.pop("pool")
    if name not in {"bulk", "selfie"}:
        raise ValueError("invalid pool")
    if operation == "configure":
        lifecycle.configure_pool(name, **request)
    elif operation == "pause":
        if type(request["paused"]) is not bool or type(request["local"]) is not bool:
            raise ValueError("invalid pause")
        lifecycle.set_claims_paused(name, **request)
    elif operation == "drain-local":
        if not lifecycle.wait_for_local_drain(name, **request):
            raise ValueError("drain timeout")
        return {"drained": True}
    elif operation == "recover":
        recover(name)
    else:
        {
            "stage": lifecycle.stage_build,
            "promote": lifecycle.promote_build,
            "cancel": lifecycle.cancel_staged_build,
        }[operation](name, **request)
    return {"ok": True}


class Command(BaseCommand):
    help = (
        "Canonical coordination using one bounded JSON request on stdin; no fleet-token authority."
    )

    def handle(self, *args, **options) -> None:
        try:
            raw = sys.stdin.read(16_385)
            if len(raw) > 16_384:
                raise ValueError("request too large")
            result = execute(json.loads(raw))
        except (ValueError, KeyError, TypeError, AttributeError, DatabaseError, ObjectDoesNotExist):
            raise CommandError("worker pool control rejected") from None
        self.stdout.write(json.dumps(result, separators=(",", ":"), sort_keys=True))
