"""Strict diagnostic observations, fenced by canonical membership and source clocks."""

from __future__ import annotations

import re
from datetime import datetime
from math import isfinite
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily

from processing.models import WorkerPool, WorkerPoolMember, WorkerPoolTelemetry
from processing.services import worker_pool_lifecycle as lifecycle
from processing.services.worker_pool_metrics import observe_pool_state

FIELDS = lifecycle.ENVELOPE_FIELDS | {
    "zone_id",
    "collector_epoch",
    "collector_started_at",
    "sequence",
    "sampled_at",
    "host",
    "container",
    "runtime",
}
HOST_FIELDS = {
    "cpu_utilization",
    "memory_available_bytes",
    "memory_total_bytes",
    "root_available_bytes",
    "root_total_bytes",
}
CONTAINER_NUMBERS = {
    "cpu_usage_cores",
    "cpu_limit_cores",
    "memory_usage_bytes",
    "memory_limit_bytes",
    "restart_count",
    "exit_code",
    "oom_events",
    "restart_events",
}
CONTAINER_FIELDS = CONTAINER_NUMBERS | {
    "present",
    "running",
    "container_id",
    "oom_killed",
    "events_available",
    "events_since",
}
KINDS = {
    "capture_metadata",
    "generate_preview",
    "generate_watermarked_preview",
    "face_embedding",
    "bib_recognition",
}
OUTCOMES = {"callback_delivered", "execution_failed", "transport_failed", "lease_lost"}
BUCKETS = ("1", "5", "15", "60", "300", "900", "1800", "+Inf")
LABELS = ["pool", "instance_id", "zone_id"]


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError()
    timestamp = datetime.fromisoformat(value)
    if not timezone.is_aware(timestamp):
        raise ValueError()
    return timestamp


def _number(value: Any, *, integer: bool = False) -> bool:
    return (
        type(value) in ((int,) if integer else (int, float))
        and isfinite(value)
        and 0 <= value <= 2**53
    )


def _object(value: Any, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= set(value) <= allowed:
        raise ValueError()
    return value


def _validate(data: dict[str, Any], now: datetime) -> lifecycle.MemberIdentity:
    if set(data) != FIELDS:
        raise ValueError()
    identity = lifecycle.MemberIdentity.parse(data)
    UUID(data["collector_epoch"])
    sampled = _time(data["sampled_at"])
    started = _time(data["collector_started_at"])
    if not lifecycle._fresh(sampled, now, lifecycle.OBSERVATION_MAX_AGE) or started > sampled:
        raise ValueError()
    if not _number(data["sequence"], integer=True) or data["sequence"] < 1:
        raise ValueError()
    if data["zone_id"] not in {"ru-central1-a", "ru-central1-b", "ru-central1-d"}:
        raise ValueError()
    host = _object(data["host"], HOST_FIELDS, set())
    if any(not _number(value) for value in host.values()) or host.get("cpu_utilization", 0) > 1:
        raise ValueError()
    for prefix in ("memory", "root"):
        available, total = host.get(prefix + "_available_bytes"), host.get(prefix + "_total_bytes")
        if available is not None and total is not None and available > total:
            raise ValueError()
    _validate_container(data["container"], sampled)
    runtime = data["runtime"]
    if runtime is None:
        return identity
    _object(
        runtime,
        {"registration_generation", "sampled_at", "busy", "aggregates"},
        {"registration_generation", "sampled_at", "busy", "aggregates"},
    )
    UUID(runtime["registration_generation"])
    if (
        not lifecycle._fresh(_time(runtime["sampled_at"]), sampled, lifecycle.OBSERVATION_MAX_AGE)
        or type(runtime["busy"]) is not int
        or runtime["busy"] not in {0, 1}
    ):
        raise ValueError()
    aggregates = _object(
        runtime["aggregates"], {"selfie_query"} if identity.pool == "selfie" else KINDS, set()
    )
    for outcomes in aggregates.values():
        _object(outcomes, OUTCOMES, set())
        for aggregate in outcomes.values():
            if not isinstance(aggregate, list) or len(aggregate) != 3:
                raise ValueError()
            count, total, buckets = aggregate
            if (
                not _number(count, integer=True)
                or not _number(total)
                or not isinstance(buckets, list)
                or len(buckets) != 8
            ):
                raise ValueError()
            if (
                any(not _number(value, integer=True) for value in buckets)
                or buckets != sorted(buckets)
                or buckets[-1] != count
            ):
                raise ValueError()
    return identity


def _validate_container(value: Any, sampled: datetime) -> None:
    if value is None:
        return
    container = _object(value, CONTAINER_FIELDS, {"present", "running", "events_available"})
    for key in ("present", "running", "oom_killed", "events_available"):
        if key in container and type(container[key]) is not bool:
            raise ValueError()
    if container["running"] and not container["present"]:
        raise ValueError()
    if "container_id" in container and (
        not isinstance(container["container_id"], str)
        or re.fullmatch(r"[0-9a-f]{64}", container["container_id"]) is None
    ):
        raise ValueError()
    if container["present"] and "container_id" not in container:
        raise ValueError()
    if any(
        not _number(
            container[key],
            integer=key in {"restart_count", "exit_code", "oom_events", "restart_events"},
        )
        for key in CONTAINER_NUMBERS & set(container)
    ):
        raise ValueError()
    if "events_since" in container and _time(container["events_since"]) > sampled:
        raise ValueError()


def _counter_progress(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if _time(current["sampled_at"]) < _time(previous["sampled_at"]):
        return False
    for kind, outcomes in previous["aggregates"].items():
        for outcome, old in outcomes.items():
            new = current["aggregates"].get(kind, {}).get(outcome)
            if (
                new is None
                or new[0] < old[0]
                or new[1] < old[1]
                or any(a < b for a, b in zip(new[2], old[2], strict=True))
            ):
                return False
    return True


@transaction.atomic
def receive(data: dict[str, Any]) -> bool:
    """Return True for a duplicate; duplicates never update receipt time."""
    now = timezone.now()
    identity = _validate(data, now)
    pool = lifecycle._pool(identity.pool)
    observed = lifecycle._observed(pool, identity.instance_id)
    if (
        not lifecycle._cloud_fresh(pool, now)
        or observed is None
        or observed["status"] in lifecycle.STOPPED
        or observed["worker_build"] != identity.worker_build
    ):
        raise lifecycle.AdmissionDenied()
    member = WorkerPoolMember.objects.filter(pool=pool, instance_id=identity.instance_id).first()
    runtime = data["runtime"]
    if member is not None and (
        member.boot_id != identity.boot_id
        or member.worker_build != identity.worker_build
        or (
            runtime is not None
            and str(member.registration_generation) != runtime["registration_generation"]
        )
    ):
        raise lifecycle.AdmissionDenied()
    if runtime is not None and member is None:
        raise lifecycle.AdmissionDenied()
    row = (
        WorkerPoolTelemetry.objects.select_for_update()
        .filter(pool=pool, instance_id=identity.instance_id)
        .first()
    )
    sampled = _time(data["sampled_at"])
    if row is not None:
        old = row.envelope
        same_epoch = UUID(old["collector_epoch"]) == UUID(data["collector_epoch"])
        same_boot = UUID(old["boot_id"]) == identity.boot_id
        if same_epoch:
            if (
                data["collector_started_at"] != old["collector_started_at"]
                or not same_boot
                or data["zone_id"] != old["zone_id"]
            ):
                raise ValueError()
            if data["sequence"] == old["sequence"] and data == old:
                return True
            if data["sequence"] <= old["sequence"]:
                raise ValueError()
        elif _time(data["collector_started_at"]) <= _time(old["collector_started_at"]):
            raise ValueError()
        if sampled <= row.sampled_at or (same_boot and data["zone_id"] != old["zone_id"]):
            raise ValueError()
        if not same_boot and member is None:
            # Pre-registration samples cannot establish a replacement boot authority.
            raise lifecycle.AdmissionDenied()
    else:
        row = WorkerPoolTelemetry(pool=pool, instance_id=identity.instance_id)
    baseline = row.runtime_baseline
    if runtime is not None:
        reset = (
            baseline is None
            or baseline["registration_generation"] != runtime["registration_generation"]
            or (row.pk is not None and UUID(row.envelope["boot_id"]) != identity.boot_id)
        )
        if not reset and not _counter_progress(baseline, runtime):
            raise ValueError()
        if reset:
            row.runtime_reset_at = sampled
        row.runtime_baseline = runtime
    container = data["container"]
    if row.pk is not None and UUID(row.envelope["boot_id"]) != identity.boot_id:
        row.container_baseline = None
        row.container_reset_at = sampled
    previous_container = row.container_baseline
    if container is not None and container["present"]:
        if previous_container is None or previous_container.get("container_id") != container.get(
            "container_id"
        ):
            row.container_reset_at = sampled
            row.container_baseline = container
        else:
            if "restart_count" in container and container["restart_count"] < previous_container.get(
                "restart_count", 0
            ):
                raise ValueError()
            # A partial observation cannot erase the last known restart-count fence.
            row.container_baseline = previous_container | container
    row.envelope = data
    row.sampled_at = sampled
    row.received_at = now
    row.save()
    return False


def generate_diagnostic_metrics() -> bytes:
    """Dedicated registry; never contributes to native queue or public application metrics."""
    registry = CollectorRegistry()
    now = timezone.now()
    families: dict[str, Any] = {}

    def gauge(name: str, value: float, labels: list[str]) -> None:
        family = families.setdefault(name, GaugeMetricFamily(name, name, labels=LABELS))
        family.add_metric(labels, value)

    def pool_gauge(name: str, value: float, pool: str) -> None:
        family = families.setdefault(name, GaugeMetricFamily(name, name, labels=["pool"]))
        family.add_metric([pool], value)

    try:
        pool_observation = observe_pool_state()
    except ValueError:
        pool_observation = None
    for pool_name in ("bulk", "selfie"):
        pool_gauge(
            "worker_pool_queue_observation_available",
            int(pool_observation is not None),
            pool_name,
        )
        if pool_observation is None:
            continue
        observed = pool_observation["pools"][pool_name]
        values = observed["metrics"]
        pool_gauge(
            "worker_pool_queue_observation_timestamp_seconds",
            timezone.datetime.fromisoformat(pool_observation["observed_at"]).timestamp(),
            pool_name,
        )
        for metric in (
            "worker_pool_claimable",
            "worker_pool_oldest_claimable_age_seconds",
            "worker_pool_workload",
        ):
            pool_gauge(metric, values[metric], pool_name)
        if observed["cloud_observed_at"] is not None:
            pool_gauge(
                "worker_pool_cloud_observation_timestamp_seconds",
                observed["cloud_observed_at"].timestamp(),
                pool_name,
            )
        if observed["native_publisher_succeeded_at"] is not None:
            pool_gauge(
                "worker_pool_native_publisher_success_timestamp_seconds",
                observed["native_publisher_succeeded_at"].timestamp(),
                pool_name,
            )
        if "worker_pool_running_instances" in values:
            pool_gauge(
                "worker_pool_running_instances",
                values["worker_pool_running_instances"],
                pool_name,
            )
        if observed["expected_instances"] is not None:
            pool_gauge(
                "worker_pool_expected_instances",
                observed["expected_instances"],
                pool_name,
            )

    for pool in WorkerPool.objects.prefetch_related("telemetry", "members"):
        cloud_fresh = lifecycle._cloud_fresh(pool, now)
        rows = {row.instance_id: row for row in pool.telemetry.all()}
        members = {member.instance_id: member for member in pool.members.all()}
        # Retain the last complete membership exclusions even when cloud data expires.
        # Its members become unknown, not fresh; historical receipts cannot resurrect nodes.
        expected = {
            item["instance_id"]: item
            for item in pool.observed_members
            if item["instance_id"] and item["status"] not in lifecycle.STOPPED
        }
        for instance, observed in expected.items():
            row = rows.get(instance)
            member = members.get(instance)
            labels = [pool.name, instance, row.envelope["zone_id"] if row else "unknown"]
            if pool.observation_completed_at is not None:
                gauge(
                    "worker_node_cloud_observation_timestamp_seconds",
                    pool.observation_completed_at.timestamp(),
                    labels,
                )
            valid = bool(
                row
                and cloud_fresh
                and observed
                and observed["worker_build"] == row.envelope["worker_build"]
                and (member is None or member.boot_id == UUID(row.envelope["boot_id"]))
            )
            host_fresh = bool(
                valid
                and row
                and lifecycle._fresh(row.sampled_at, now, lifecycle.OBSERVATION_MAX_AGE)
                and lifecycle._fresh(row.received_at, now, lifecycle.OBSERVATION_MAX_AGE)
            )
            gauge("worker_cloud_observation_fresh", int(cloud_fresh), labels)
            gauge("worker_host_observation_fresh", int(host_fresh), labels)
            gauge("worker_host_observation_missing", int(row is None), labels)
            gauge(
                "worker_host_collection_available",
                int(host_fresh and row is not None and set(row.envelope["host"]) == HOST_FIELDS),
                labels,
            )
            gauge(
                "worker_container_observation_available",
                int(host_fresh and row is not None and row.envelope["container"] is not None),
                labels,
            )
            if row:
                gauge(
                    "worker_host_observation_age_seconds",
                    max(0, (now - row.sampled_at).total_seconds()),
                    labels,
                )
            runtime = row.envelope["runtime"] if row else None
            runtime_fresh = bool(
                host_fresh
                and runtime
                and member
                and str(member.registration_generation) == runtime["registration_generation"]
                and lifecycle._fresh(
                    _time(runtime["sampled_at"]), now, lifecycle.OBSERVATION_MAX_AGE
                )
            )
            gauge("worker_runtime_observation_fresh", int(runtime_fresh), labels)
            gauge(
                "worker_runtime_scrape_available", int(host_fresh and runtime is not None), labels
            )
            if row and row.runtime_baseline:
                gauge(
                    "worker_runtime_observation_age_seconds",
                    max(0, (now - _time(row.runtime_baseline["sampled_at"])).total_seconds()),
                    labels,
                )
            if member and cloud_fresh:
                gauge("worker_coordinator_ready", int(member.ready), labels)
                gauge(
                    "worker_coordinator_serving", int(lifecycle._serving(pool, member, now)), labels
                )
                gauge("worker_coordinator_draining", int(member.draining), labels)
                gauge(
                    "worker_coordinator_heartbeat_fresh",
                    int(lifecycle._fresh(member.heartbeat_at, now, lifecycle.HEARTBEAT_MAX_AGE)),
                    labels,
                )
                if member.heartbeat_at:
                    gauge(
                        "worker_coordinator_heartbeat_age_seconds",
                        max(0, (now - member.heartbeat_at).total_seconds()),
                        labels,
                    )
            if host_fresh and row:
                for key, value in row.envelope["host"].items():
                    gauge("worker_host_" + key, value, labels)
                container = row.envelope["container"]
                for key, value in (container or {}).items():
                    if key not in {"container_id", "events_since"}:
                        gauge("worker_container_" + key, value, labels)
                if container is not None and "events_since" in container:
                    gauge(
                        "worker_container_events_window_seconds",
                        (
                            row.sampled_at - _time(row.envelope["container"]["events_since"])
                        ).total_seconds(),
                        labels,
                    )
                if container is not None and row.container_reset_at:
                    gauge(
                        "worker_container_reset_timestamp_seconds",
                        row.container_reset_at.timestamp(),
                        labels,
                    )
            if runtime_fresh and row and runtime:
                gauge("worker_runtime_busy", runtime["busy"], labels)
                if row.runtime_reset_at:
                    gauge(
                        "worker_runtime_reset_timestamp_seconds",
                        row.runtime_reset_at.timestamp(),
                        labels,
                    )
                for kind, outcomes in runtime["aggregates"].items():
                    for outcome, (count, total, buckets) in outcomes.items():
                        names = LABELS + ["kind", "outcome"]
                        counter = families.setdefault(
                            "worker_runtime_executions",
                            CounterMetricFamily(
                                "worker_runtime_executions",
                                "Terminal runtime observations",
                                labels=names,
                            ),
                        )
                        counter.add_metric(labels + [kind, outcome], count)
                        histogram = families.setdefault(
                            "worker_runtime_execution_duration_seconds",
                            HistogramMetricFamily(
                                "worker_runtime_execution_duration_seconds",
                                "Execution through delivery and cleanup",
                                labels=names,
                            ),
                        )
                        histogram.add_metric(
                            labels + [kind, outcome],
                            list(zip(BUCKETS, buckets, strict=True)),
                            total,
                        )

    class Collector:
        def collect(self):
            return iter(families.values())

    registry.register(Collector())
    return generate_latest(registry)
