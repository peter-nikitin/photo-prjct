#!/usr/bin/env python3
"""Probe the public health endpoint and write bounded Yandex Monitoring metrics."""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass

MONITORING_WRITE_URL = "https://monitoring.api.cloud.yandex.net/monitoring/v2/data/write"
EXPECTED_HEALTH_BODY = {"status": "ok"}
DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ProbeConfig:
    target: str
    folder_id: str
    environment: str
    check_name: str
    api_key: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class HealthResponse:
    status: int
    body: bytes


Metric = dict[str, object]
FetchHealth = Callable[[str, float], HealthResponse]
CertificateNotAfter = Callable[[str, float], str]
MetricWriter = Callable[[ProbeConfig, list[Metric]], None]


def fetch_health(target: str, timeout_seconds: float) -> HealthResponse:
    """Fetch an HTTPS health response with Python's trusted default TLS context."""
    request = urllib.request.Request(
        target,
        headers={"Accept": "application/json", "User-Agent": "findme-public-health-probe/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return HealthResponse(status=response.status, body=response.read())
    except urllib.error.HTTPError as error:
        return HealthResponse(status=error.code, body=error.read())


def certificate_not_after(target: str, timeout_seconds: float) -> str:
    """Return the trusted leaf certificate expiry time for an HTTPS target."""
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("target must be an HTTPS URL with a hostname")

    port = parsed.port or 443
    context = ssl.create_default_context()
    with socket.create_connection((parsed.hostname, port), timeout=timeout_seconds) as connection:
        with context.wrap_socket(connection, server_hostname=parsed.hostname) as tls_connection:
            not_after = tls_connection.getpeercert().get("notAfter")

    if not isinstance(not_after, str):
        raise ValueError("trusted certificate has no expiry time")
    return not_after


def certificate_days_remaining(not_after: str, now: float) -> float:
    return (ssl.cert_time_to_seconds(not_after) - now) / 86_400


def _health_is_expected(response: HealthResponse) -> tuple[bool, str | None]:
    if response.status != 200:
        return False, "unexpected HTTP status"
    try:
        body = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, "unexpected health response"
    if body != EXPECTED_HEALTH_BODY:
        return False, "unexpected health response"
    return True, None


def build_metrics(
    config: ProbeConfig,
    *,
    success: bool,
    duration_seconds: float,
    tls_days_remaining: float | None,
) -> list[Metric]:
    labels = {"environment": config.environment, "check": config.check_name}
    metrics: list[Metric] = [
        {
            "name": "findme_probe_success",
            "labels": labels,
            "value": 1.0 if success else 0.0,
            "type": "DGAUGE",
        },
        {
            "name": "findme_probe_duration_seconds",
            "labels": labels,
            "value": duration_seconds,
            "type": "DGAUGE",
        },
    ]
    if tls_days_remaining is not None:
        metrics.append(
            {
                "name": "findme_probe_tls_days_remaining",
                "labels": labels,
                "value": tls_days_remaining,
                "type": "DGAUGE",
            }
        )
    return metrics


def write_metrics(config: ProbeConfig, metrics: list[Metric]) -> None:
    parameters = urllib.parse.urlencode({"folderId": config.folder_id, "service": "custom"})
    request = urllib.request.Request(
        f"{MONITORING_WRITE_URL}?{parameters}",
        data=json.dumps({"metrics": metrics}, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Api-Key {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
        response.read()


def _failure_reason(error: BaseException) -> str:
    if isinstance(error, ssl.SSLCertVerificationError):
        return "TLS certificate validation failed"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "connection timed out"
    if isinstance(error, urllib.error.URLError):
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            return "TLS certificate validation failed"
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            return "connection timed out"
        return "connection failed"
    if isinstance(error, ValueError):
        return "invalid HTTPS target or certificate"
    return "probe request failed"


def run_probe(
    config: ProbeConfig,
    *,
    fetch_health: FetchHealth = fetch_health,
    certificate_not_after: CertificateNotAfter = certificate_not_after,
    monotonic_clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
    metric_writer: MetricWriter = write_metrics,
    emit: Callable[[str], None] = print,
) -> int:
    """Run one probe, always attempting the metric write after a completed attempt."""
    started_at = monotonic_clock()
    success = False
    tls_days: float | None = None
    failure: str | None = None

    try:
        response = fetch_health(config.target, config.timeout_seconds)
        success, failure = _health_is_expected(response)
        try:
            tls_days = certificate_days_remaining(
                certificate_not_after(config.target, config.timeout_seconds), wall_clock()
            )
        except (OSError, ssl.SSLError, ValueError, urllib.error.URLError) as error:
            success = False
            failure = _failure_reason(error)
            tls_days = None
    except (OSError, ssl.SSLError, ValueError, urllib.error.URLError) as error:
        failure = _failure_reason(error)

    duration_seconds = max(0.0, monotonic_clock() - started_at)
    metrics = build_metrics(
        config,
        success=success,
        duration_seconds=duration_seconds,
        tls_days_remaining=tls_days,
    )
    try:
        metric_writer(config, metrics)
    except (OSError, ssl.SSLError, ValueError, urllib.error.URLError, RuntimeError):
        emit("metrics write failed")
        return 1

    if failure is not None:
        emit(f"probe failed: {failure}")
        return 1

    emit("probe succeeded")
    return 0


def parse_arguments(arguments: Sequence[str] | None = None) -> ProbeConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--folder-id", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--check", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(arguments)

    parsed = urllib.parse.urlsplit(args.target)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        parser.error("--target must be a secret-free HTTPS URL")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")

    return ProbeConfig(
        target=args.target,
        folder_id=args.folder_id,
        environment=args.environment,
        check_name=args.check,
        api_key=args.api_key,
        timeout_seconds=args.timeout_seconds,
    )


def main(arguments: Sequence[str] | None = None) -> int:
    return run_probe(parse_arguments(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
