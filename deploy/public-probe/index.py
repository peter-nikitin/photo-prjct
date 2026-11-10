"""Independent public HTTPS observation, invoked by a Cloud Functions timer."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests
from monitor_public_health import HealthResponse, _health_is_expected, fetch_health

TARGET = "https://findme-photo.ru/health/"
LABELS = {"check": "canonical-health", "instance": "findme-photo-app"}


@dataclass(frozen=True)
class Observation:
    success: bool
    duration_seconds: float
    observed_at: float


def observe() -> Observation:
    started = time.perf_counter()
    try:
        response: HealthResponse = fetch_health(TARGET, 5)
        success, _ = _health_is_expected(response)
    except OSError:
        success = False
    return Observation(success, time.perf_counter() - started, time.time())


def publish(observation: Observation, *, token: str, workspace_id: str) -> None:
    import remote_pb2
    import snappy

    values = {
        "findme_probe_success": float(observation.success),
        "findme_probe_duration_seconds": observation.duration_seconds,
        "findme_probe_observed_timestamp_seconds": observation.observed_at,
    }
    payload = remote_pb2.WriteRequest()
    for name, value in values.items():
        series = payload.timeseries.add()
        for key, label in sorted({"__name__": name, **LABELS}.items()):
            series.labels.add(name=key, value=label)
        series.samples.add(timestamp=int(observation.observed_at * 1000), value=value)
    try:
        response = requests.post(
            "https://monitoring.api.cloud.yandex.net/prometheus/workspaces/"
            + workspace_id
            + "/api/v1/write",
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/x-protobuf",
                "Content-Encoding": "snappy",
                "X-Prometheus-Remote-Write-Version": "0.1.0",
                "User-Agent": "findme-photo-public-probe/1",
            },
            data=snappy.compress(payload.SerializeToString()),
            timeout=5,
        )
    except requests.RequestException:
        raise RuntimeError("Public probe metric publication failed") from None
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Public probe metric publication failed (HTTP {response.status_code})")


def handler(event, context):  # noqa: ARG001
    observation = observe()
    publish(
        observation,
        token=context.token["access_token"],
        workspace_id=os.environ["PROMETHEUS_WORKSPACE_ID"],
    )
    # A measured outage is a successful observation, not a reason for trigger retries.
    return {"success": observation.success}
