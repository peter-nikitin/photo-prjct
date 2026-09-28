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
    _cloud_fresh,
    record_queue_observation,
)
from processing.services.worker_pool_state import build_worker_pool_state


def observe_metrics(zone: str) -> dict[str, Any]:
    identifier(zone)
    state = build_worker_pool_state()
    if not state["endpoint_enabled"]:
        raise ValueError("worker endpoint unavailable")
    if settings.PHOTO_WORKER_POOL_COORDINATOR_ENABLED and (
        not settings.PHOTO_PROCESSING_FLEET_TOKEN
        or settings.PHOTO_PROCESSING_FLEET_TOKEN == settings.PHOTO_PROCESSING_WORKER_TOKEN
    ):
        raise ValueError("fleet endpoint unavailable")
    metrics: list[dict[str, object]] = []
    observed_at = timezone.datetime.fromisoformat(state["observed_at"])
    capacity = {pool.name: pool for pool in WorkerPool.objects.all()}
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
        observation = capacity.get(name)
        fresh = bool(observation and _cloud_fresh(observation, observed_at))
        values["worker_pool_capacity_fresh"] = int(fresh)
        # Missing/stale membership is unknown, not zero actual capacity. Never use target size
        # or workload as a substitute for the trusted complete current cloud observation.
        if fresh and observation is not None:
            values["worker_pool_running_instances"] = sum(
                row["status"] in RUNNING for row in observation.observed_members
            )
        metrics.extend(
            {
                "name": metric,
                "labels": {"pool": name, "zone_id": zone},
                "type": "DGAUGE",
                "value": value,
            }
            for metric, value in values.items()
        )
    return {"observed_at": state["observed_at"], "metrics": metrics, "published": False}


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
