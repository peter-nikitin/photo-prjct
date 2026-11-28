#!/usr/bin/env python3
"""Reconcile the single managed Prometheus rule file and alert routing."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.queue_catalog import QueueDefinition

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("monitoring_control", HERE / "control.py")
assert spec and spec.loader
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
RULE_NAME = "findme-photo.yml"
INSTANCE = "findme-photo-app"


def _catalog() -> tuple[QueueDefinition, ...]:
    path = HERE.parents[2] / "src/backend/config/queue_catalog.py"
    spec = importlib.util.spec_from_file_location("monitoring_alert_catalog", path)
    assert spec and spec.loader
    catalog = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = catalog
    spec.loader.exec_module(catalog)
    return catalog.QUEUES


SOURCE_SELECTORS = (
    {"__name__": "findme_probe_success", "check": "canonical-health", "instance": INSTANCE},
    {
        "__name__": "findme_probe_observed_timestamp_seconds",
        "check": "canonical-health",
        "instance": INSTANCE,
    },
    *(
        {
            "__name__": "findme_queue_due_observed_timestamp_seconds",
            "queue": queue,
            "instance": INSTANCE,
        }
        for queue in (definition.key for definition in _catalog())
    ),
    {"__name__": "findme_db_usable", "instance": INSTANCE},
    {"__name__": "findme_db_check_timestamp_seconds", "instance": INSTANCE},
    *(
        {"__name__": metric, "instance": INSTANCE, "mountpoint": mountpoint}
        for mountpoint in ("/", "/var/lib/findme-photo")
        for metric in ("sys_filesystem_FreeB", "sys_filesystem_SizeB")
    ),
    {"__name__": "findme_http_requests_total", "job": "findme-http"},
)
EXPLICIT_OBSERVATION_NAMES = {
    "findme_probe_observed_timestamp_seconds",
    "findme_queue_due_observed_timestamp_seconds",
    "findme_db_check_timestamp_seconds",
}


class MissingSource(control.ControlError):
    """A source absent or stale during bounded preflight; eligible for retry."""


def check_sources(transport, *, now: float | None = None) -> None:
    observed = time.time() if now is None else now
    results = transport.read_samples(list(SOURCE_SELECTORS), now=observed)
    if len(results) != len(SOURCE_SELECTORS):
        raise control.ControlError("Monitoring preflight result count mismatch")
    for selector, series_set in zip(SOURCE_SELECTORS, results, strict=True):
        latest = [max(series, key=lambda point: point[0]) for series in series_set if series]
        valid = [
            (timestamp, value)
            for timestamp, value in latest
            if math.isfinite(timestamp)
            and math.isfinite(value)
            and 0 <= observed - timestamp < 300
            and (
                selector["__name__"] not in EXPLICIT_OBSERVATION_NAMES
                or 0 <= observed - value < 300
            )
        ]
        if not valid:
            raise MissingSource(f"fresh monitoring source missing: {selector['__name__']}")


def wait_for_sources(transport) -> None:
    deadline = time.monotonic() + 600
    while True:
        try:
            check_sources(transport)
            return
        except MissingSource:
            if time.monotonic() >= deadline:
                raise control.ControlError(
                    "fresh monitoring sources unavailable; old rules retained"
                ) from None
            time.sleep(10)


def _queue_observation_expression(queues: tuple[QueueDefinition, ...]) -> str:
    # The expected series exist even if all application and collector samples disappear.
    expected = " or ".join(
        f'label_replace(label_replace(vector(1), "queue", "{queue.key}", "", ""), '
        f'"queue_title", "{queue.title}", "", "")'
        for queue in queues
    )
    telemetry = f'job="findme-queue-telemetry",instance="{INSTANCE}"'
    sources = []
    for source, job in (
        ("work", "findme-queue-telemetry"),
        ("ready", "findme-http"),
        ("due", "findme-http"),
        ("results", "findme-queue-telemetry"),
    ):
        age = (
            "time() - max by(queue) "
            f'(findme_queue_{source}_observed_timestamp_seconds{{job="{job}",instance="{INSTANCE}"}})'
        )
        limit = "< 300" if source == "due" else "<= 150"
        healthy = f"(({age}) >= 0) and on(queue) (({age}) {limit})"
        if source == "work":
            healthy += (
                f" and on(queue) (max by(queue) (findme_queue_work_available{{{telemetry}}}) == 1)"
            )
        if source == "results":
            healthy += (
                f" and on() (max(findme_queue_results_listener_connected{{{telemetry}}}) == 1)"
            )
        sources.append(
            f"label_replace((({expected}) unless on(queue) ({healthy})), "
            f'"source", "{source}", "", "")'
        )
    return "\n or ".join(sources)


def render_rules(queues: tuple[QueueDefinition, ...] | None = None) -> bytes:
    import yaml

    rules = yaml.safe_load((HERE / "rules.yml").read_text())
    critical = next(group for group in rules["groups"] if group["name"] == "findme-critical")
    queues = _catalog() if queues is None else queues
    stalled = []
    for queue in queues:
        threshold = (
            300
            if queue.key in {"selfie_recognition", "selfie_matching", "gallery_matching"}
            else 900
        )
        selector = f'queue="{queue.key}"'
        expression = (
            f"(findme_queue_oldest_due_age_seconds{{{selector}}} > {threshold})"
            " and on(queue,instance) (time() - "
            f"findme_queue_last_success_timestamp_seconds{{{selector}}} > {threshold})"
            f" and on(queue,instance) (findme_queue_due_count{{{selector}}} > 0)"
            " and on(queue,instance) (time() - "
            f"findme_queue_due_observed_timestamp_seconds{{{selector}}} >= 0)"
            " and on(queue,instance) (time() - "
            f"findme_queue_due_observed_timestamp_seconds{{{selector}}} < 300)"
        )
        stalled.append(
            f'label_replace(label_replace(({expression}), "queue_title", "{queue.title}", "", ""), '
            f'"stall_threshold", "{threshold}", "", "")'
        )
    critical["rules"].append(
        {
            "alert": "ReadyWorkStalled",
            "expr": "\n or ".join(stalled),
            "for": "5m",
            "labels": {
                "severity": "critical",
                "project": "findme-photo",
                "notification": "actionable",
                "incident": "queue",
            },
            "annotations": {
                "summary": "Очередь „{{ $labels.queue_title }}“ "
                "({{ $labels.queue }}) не продвигается",
                "description": "Готовая работа и последнее успешное завершение старше "
                "{{ $labels.stall_threshold }} секунд. Состояние сохраняется не менее 5 минут.",
            },
        }
    )
    critical["rules"].append(
        {
            "alert": "QueueObservationUnavailable",
            "expr": _queue_observation_expression(queues),
            "for": "5m",
            "labels": {
                "instance": INSTANCE,
                "severity": "critical",
                "project": "findme-photo",
                "notification": "actionable",
                "incident": "queue-observation",
            },
            "annotations": {
                "summary": "Недоступен источник наблюдения очереди "
                "„{{ $labels.queue_title }}“ ({{ $labels.queue }})",
                "description": "Источник {{ $labels.source }}. "
                "Состояние сохраняется не менее 5 минут.",
            },
        }
    )
    return yaml.safe_dump(rules, allow_unicode=True, sort_keys=False).encode()


def validate_rules(promtool: str, desired: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix=".monitoring-alerts-", dir=Path.cwd()) as directory:
        rendered = Path(directory) / "rules.yml"
        rendered.write_bytes(desired)
        fixtures = Path(directory) / "rules.test.yml"
        fixtures.write_bytes((HERE / fixtures.name).read_bytes())
        subprocess.run([promtool, "check", "rules", str(rendered)], check=True)
        subprocess.run([promtool, "test", "rules", str(fixtures)], check=True)


def _expected_rules(desired: bytes) -> dict[str, set[str]]:
    import yaml

    try:
        groups = yaml.safe_load(desired)["groups"]
        expected = {group["name"]: {rule["alert"] for rule in group["rules"]} for group in groups}
    except (KeyError, TypeError, ValueError):
        raise control.ControlError("managed rule file invalid") from None
    if not expected or any(not rules for rules in expected.values()):
        raise control.ControlError("managed rule file contains no alert rules")
    return expected


def reconcile_rules(transport, desired: bytes, backup: Path) -> None:
    expected = _expected_rules(desired)
    current = transport.rule_file(RULE_NAME)
    if current.get("name") != RULE_NAME:
        raise control.ControlError("managed rule file identity mismatch")
    try:
        previous = base64.b64decode(current["content"], validate=True)
    except (KeyError, ValueError):
        raise control.ControlError("managed rule file content invalid") from None
    required_after_ms = int((time.time() - 300) * 1000)
    if previous != desired:
        backup.mkdir(mode=0o700, parents=True, exist_ok=False)
        (backup / RULE_NAME).write_bytes(previous)
        required_after_ms = int(time.time() * 1000)
        transport.put_rule_file(RULE_NAME, desired)
        readback = transport.rule_file(RULE_NAME)
        try:
            actual = base64.b64decode(readback["content"], validate=True)
        except (KeyError, ValueError):
            raise control.ControlError(
                "managed rule file read-back invalid; inspect backup"
            ) from None
        if readback.get("name") != RULE_NAME or actual != desired:
            raise control.ControlError("managed rule file read-back mismatch; inspect backup")
    deadline = time.monotonic() + 360
    while True:
        snapshots = transport.rule_snapshots(RULE_NAME)
        groups = snapshots.get("snapshotByGroup")
        if not isinstance(groups, dict):
            raise control.ControlError("managed rule file snapshots unavailable; inspect backup")
        rows = [row for group in groups.values() for row in group]
        actual = {
            group: {row.get("record") for row in entries} for group, entries in groups.items()
        }
        coverage = actual == expected and len(rows) == sum(map(len, expected.values()))
        fresh = coverage and all(
            row.get("evaluatedAtTimeEpochMs", 0) >= required_after_ms for row in rows
        )
        if fresh and all(row.get("state") == "OK" for row in rows):
            return
        if fresh and any(row.get("state") not in {"OK", "NOT_EVALUATED_YET"} for row in rows):
            raise control.ControlError("managed rule file evaluation failed; inspect backup")
        if time.monotonic() >= deadline:
            raise control.ControlError(
                "managed rule file fresh evaluation timed out; inspect backup"
            )
        time.sleep(10)


def publish_alerts(transport, rules: bytes, routing: bytes, backup: Path) -> None:
    wait_for_sources(transport)
    reconcile_rules(transport, rules, backup)
    # The alertmanager API documents PUT but no GET/read-back. Git is the recovery source.
    transport.put_alertmanager(routing)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--promtool")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--oidc-config", type=Path)
    args = parser.parse_args()
    try:
        if args.validate:
            if not args.promtool:
                parser.error("--promtool is required for validation")
            validate_rules(args.promtool, render_rules())
            import yaml

            yaml.safe_load((HERE / "alertmanager.yml").read_text())
            return 0
        if args.backup is None or args.oidc_config is None:
            parser.error("--backup and --oidc-config are required for reconciliation")
        transport = control.CloudTransport(
            control.load_config(), control.identity("github-oidc", args.oidc_config)
        )
        publish_alerts(
            transport,
            render_rules(),
            (HERE / "alertmanager.yml").read_bytes(),
            args.backup,
        )
        return 0
    except (control.ControlError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Alert reconciliation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
