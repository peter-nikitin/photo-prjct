"""Queue workload for native autoscaling, independently of worker liveness."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from processing.models import WorkerPool
from processing.services.worker_pool_cloud import identifier, write_metrics
from processing.services.worker_pool_lifecycle import (
    RUNNING,
    STOPPED,
    _cloud_fresh,
    record_queue_observation,
)
from processing.services.worker_pool_state import build_worker_pool_state


def observe_pool_state(*, capacity: list[WorkerPool] | None = None) -> dict[str, Any]:
    """Build one read-only queue/capacity observation for both supported pools."""
    state = build_worker_pool_state()
    if not state["endpoint_enabled"]:
        raise ValueError("worker endpoint unavailable")
    if settings.PHOTO_WORKER_POOL_COORDINATOR_ENABLED and not settings.PHOTO_PROCESSING_FLEET_TOKEN:
        raise ValueError("fleet endpoint unavailable")
    capacity_by_pool = {
        pool.name: pool for pool in (capacity if capacity is not None else WorkerPool.objects.all())
    }
    # Queue provenance predates its aggregate reads; capacity may commit during those reads.
    # Evaluate the materialized cloud rows against their own completed-read clock.
    capacity_observed_at = timezone.now()
    pools: dict[str, dict[str, Any]] = {}
    for name, pool in state["pools"].items():
        leases = pool["leases"]
        if leases["missing_expiry"]:
            raise ValueError("queue ownership unavailable")
        values = {
            "worker_pool_workload": pool["claimable"] + leases["active"] + leases["expired"],
            "worker_pool_claimable": pool["claimable"],
            "worker_pool_active_leases": leases["active"],
            "worker_pool_recoverable_leases": leases["expired"],
            "worker_pool_oldest_claimable_age_seconds": pool["oldest_claimable_age_seconds"],
            "worker_pool_failed_jobs": pool["jobs"]["failed"],
            "worker_pool_observed_timestamp": timezone.datetime.fromisoformat(
                state["observed_at"]
            ).timestamp(),
        }
        observation = capacity_by_pool.get(name)
        fresh = bool(observation and _cloud_fresh(observation, capacity_observed_at))
        values["worker_pool_capacity_fresh"] = int(fresh)
        # Missing/stale membership is unknown, not zero actual capacity. Never use target size
        # or workload as a substitute for the trusted complete current cloud observation.
        if fresh and observation is not None:
            values["worker_pool_running_instances"] = sum(
                row["status"] in RUNNING for row in observation.observed_members
            )
        pools[name] = {
            "metrics": values,
            "cloud_observed_at": (
                observation.observation_completed_at if observation is not None else None
            ),
            "native_publisher_succeeded_at": (
                observation.queue_observed_at if observation is not None else None
            ),
            "expected_instances": (
                sum(
                    bool(row["instance_id"]) and row["status"] not in STOPPED
                    for row in observation.observed_members
                )
                if fresh and observation is not None
                else None
            ),
        }
    return {"observed_at": state["observed_at"], "pools": pools}


def observe_metrics(zone: str) -> dict[str, Any]:
    identifier(zone)
    observation = observe_pool_state()
    metrics: list[dict[str, object]] = []
    for name, pool in observation["pools"].items():
        metrics.extend(
            {
                "name": metric,
                "labels": {"pool": name, "zone_id": zone},
                "type": "DGAUGE",
                "value": value,
            }
            for metric, value in pool["metrics"].items()
        )
    return {
        "observed_at": observation["observed_at"],
        "metrics": metrics,
        "published": False,
    }


def publish_metrics(zone: str, folder_id: str) -> dict[str, Any]:
    result = observe_metrics(zone)
    write_metrics(folder_id, result["metrics"])
    # Only a successful complete Monitoring response may authorize fresh queue evidence.
    with transaction.atomic():
        for name in ("bulk", "selfie"):
            if not record_queue_observation(
                name,
                observed_at=timezone.datetime.fromisoformat(result["observed_at"]),
                endpoint_available=True,
            ):
                raise ValueError("queue observation rejected")
    result["published"] = True
    return result
