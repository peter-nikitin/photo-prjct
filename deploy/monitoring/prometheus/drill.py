#!/usr/bin/env python3
"""Bounded, explicitly synthetic worker-alert evaluator rehearsal."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import control

DRILL_RULES = "findme-worker-activation-drill.yml"
DRILL_PATH = "/extensions/v1/rules/" + DRILL_RULES
RUN_LABEL = "worker-activation"
HEALTHY_END = 4 * 60
SATURATED_END = 13 * 60
MISSING_END = 17 * 60
STALE_END = 21 * 60
RECOVERED_END = 25 * 60
RUN_END = 29 * 60
EXPIRY_SECONDS = 32 * 60
EVALUATION_MAX_AGE = 300  # Includes the provider's two-minute evaluation delay.
POLL_SECONDS = 30
REQUIRED_EVIDENCE = frozenset(
    {
        "healthy",
        "saturation-pending",
        "saturation-firing",
        "source-missing",
        "retained-stale",
        "recovered",
    }
)
EXPECTED_ALERTS = (
    "WorkerReadyWorkOverdue",
    "WorkerPoolSaturated",
    "WorkerQueueObservationMissing",
    "WorkerCloudObservationMissing",
    "WorkerNativePublisherMissing",
    "WorkerHostDiagnosticsMissing",
    "WorkerRuntimeDiagnosticsMissing",
)
EXPECTED_PREDICATE_HASHES = (
    "46b820497b26180035e803fb34b88392d83fc3e39704a5088424d873c4bfd752",
    "e016b11b030d1deef4eeedb758f0c842cfeca0070246a6f9307b87f42984741c",
    "ecf4608e18014ac2a56efcf251553d166a4331eb3552902628da1721d77e2d40",
    "738a330830cad7b867e70cf3da18bda8517cc2ea172d0cf00af55902bee20ca1",
    "2e79521287c18a0fb02ba8d8f09c7a4d4250589dc710e8f3788a9755c1ed5078",
    "64fffa1fa6505945e37f8580cb1a778fd8e3f0ce0142a21dae3a567da7d6b1a1",
    "411eecf16994eaafc5b61df165fb4515cab7f370f1767b2013d9ba386c8f6b3a",
)
POOL_METRICS = {
    "worker_pool_queue_observation_available",
    "worker_pool_queue_observation_timestamp_seconds",
    "worker_pool_cloud_observation_timestamp_seconds",
    "worker_pool_native_publisher_success_timestamp_seconds",
    "worker_pool_oldest_claimable_age_seconds",
    "worker_pool_running_instances",
    "worker_pool_expected_instances",
}
NODE_METRICS = {
    "worker_node_cloud_observation_timestamp_seconds",
    "worker_host_observation_missing",
    "worker_host_observation_fresh",
    "worker_host_collection_available",
    "worker_runtime_scrape_available",
    "worker_runtime_observation_fresh",
}
METRICS = POOL_METRICS | NODE_METRICS
METRIC_TOKEN = re.compile(r"\b(worker_[A-Za-z0-9_]+)(\{[^{}]*\})?(\[[^\]]+\])?")


class DrillError(Exception):
    """Safe operator error without transport bodies or credential material."""


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _phase(start: int, begin: int, end: int) -> str:
    return f"((time() >= bool {start + begin}) * (time() < bool {start + end}))"


def _pool_vector(pool: str, value: str) -> str:
    return f'label_replace(vector({value}), "pool", "{pool}", "", "")'


def _node_vector(pool: str, value: str) -> str:
    return (
        "label_replace(label_replace("
        + _pool_vector(pool, value)
        + f', "instance_id", "drill-{pool}", "", ""), '
        '"zone_id", "drill-zone", "", "")'
    )


def _present(vector: str, start: int, begin: int, end: int) -> str:
    return (
        f"({vector}) and on() "
        f"((vector(time()) < {start + begin}) or "
        f"(vector(time()) >= {start + end}))"
    )


def _metric_vector(metric: str, pool: str, start: int) -> str:
    stale = _phase(start, MISSING_END, STALE_END)
    saturation = _phase(start, HEALTHY_END, SATURATED_END)
    source_clock = "time()"
    if pool == "selfie":
        source_clock = f"(time() - ({stale}) * (time() - {start + MISSING_END}))"
    if metric in {
        "worker_pool_queue_observation_timestamp_seconds",
        "worker_pool_cloud_observation_timestamp_seconds",
        "worker_pool_native_publisher_success_timestamp_seconds",
    }:
        value = source_clock
    elif metric == "worker_pool_oldest_claimable_age_seconds":
        value = f"({saturation}) * (301 + time() - {start + HEALTHY_END})"
    elif metric == "worker_host_observation_missing":
        value = stale if pool == "bulk" else "0"
    elif metric in {
        "worker_host_observation_fresh",
        "worker_host_collection_available",
        "worker_runtime_scrape_available",
        "worker_runtime_observation_fresh",
    }:
        value = f"(1 - {stale})" if pool == "bulk" else "1"
    elif metric == "worker_node_cloud_observation_timestamp_seconds":
        value = (
            f"(time() - ({stale}) * (time() - {start + MISSING_END}))"
            if pool == "bulk"
            else "time()"
        )
    else:
        value = "1"
    vector = _node_vector(pool, value) if metric in NODE_METRICS else _pool_vector(pool, value)
    if pool == "bulk" and metric in {
        "worker_pool_queue_observation_available",
        "worker_pool_queue_observation_timestamp_seconds",
        "worker_pool_cloud_observation_timestamp_seconds",
        "worker_pool_native_publisher_success_timestamp_seconds",
    }:
        return _present(vector, start, SATURATED_END, MISSING_END)
    if pool == "selfie" and metric in NODE_METRICS - {
        "worker_node_cloud_observation_timestamp_seconds"
    }:
        return _present(vector, start, SATURATED_END, MISSING_END)
    return vector


def _synthetic_metric(metric: str, start: int) -> str:
    return (
        "(" + " or ".join(_metric_vector(metric, pool, start) for pool in ("bulk", "selfie")) + ")"
    )


def _substitute(expression: str, start: int) -> str:
    def replacement(match: re.Match[str]) -> str:
        metric, selector, window = match.groups()
        if metric not in METRICS:
            raise DrillError("unknown worker metric in reviewed predicate")
        if selector or (window and window != "[90s]"):
            raise DrillError("unknown worker selector or range in reviewed predicate")
        vector = _synthetic_metric(metric, start)
        return f"({vector})[90s:30s]" if window else vector

    # Parenthesized synthetic RHS otherwise looks like group_left(label-list).
    result = METRIC_TOKEN.sub(replacement, expression.replace("group_left ", "group_left() "))
    if METRIC_TOKEN.search(result):
        raise DrillError("production worker series remain in synthetic predicate")
    return result


def render_rules(control_module: Any, config: dict[str, Any], run_id: str, start: int) -> str:
    import yaml

    if not config.get("worker_alerts_enabled"):
        raise DrillError("worker alert profile must be Git-enabled and live-applied")
    if not re.fullmatch(r"[0-9]{1,20}", run_id) or start < 0:
        raise DrillError("invalid drill run identity or start")
    groups = yaml.safe_load(control_module.render(config)["rules.yml"])["groups"]
    worker = [group for group in groups if group.get("name") == "findme-workers"]
    if (
        len(worker) != 1
        or tuple(rule.get("alert") for rule in worker[0]["rules"]) != EXPECTED_ALERTS
    ):
        raise DrillError("reviewed worker alert identities changed")
    group = {
        "name": "findme-worker-activation-drill",
        "interval": worker[0]["interval"],
        "rules": [],
    }
    for original, predicate_hash in zip(worker[0]["rules"], EXPECTED_PREDICATE_HASHES, strict=True):
        synthetic = _substitute(original["expr"], start)
        if _hash(original["expr"]) != predicate_hash:
            raise DrillError("reviewed worker predicate changed")
        rule = dict(original)
        rule["expr"] = f"({synthetic}) and on() (vector(time()) < {start + EXPIRY_SECONDS})"
        rule["labels"] = {
            **original["labels"],
            "drill": RUN_LABEL,
            "drill_run": run_id,
            "drill_case": (
                "saturation"
                if original["alert"] in EXPECTED_ALERTS[:2]
                else "source-missing"
                if original["alert"] in EXPECTED_ALERTS[2:5]
                else "node-diagnostics"
            ),
        }
        rule["annotations"] = {
            **original["annotations"],
            "summary": "SYNTHETIC DRILL — " + original["annotations"]["summary"],
        }
        group["rules"].append(rule)
    return yaml.safe_dump({"groups": [group]}, sort_keys=False)


def write_promtool_fixture(rules_path: Path) -> Path:
    import yaml

    rules = yaml.safe_load(rules_path.read_text())["groups"][0]["rules"]
    by_alert = {rule["alert"]: rule for rule in rules}
    checks = json.loads((Path(__file__).parent / "drill-rule-tests.json").read_text())["checks"]
    cases = []
    for check in checks:
        for alert in EXPECTED_ALERTS:
            rule = by_alert[alert]
            cases.append(
                {
                    "eval_time": check["at"],
                    "alertname": alert,
                    "exp_alerts": [
                        {
                            "exp_labels": {**rule["labels"], "pool": pool},
                            "exp_annotations": rule["annotations"],
                        }
                        for pool in check["firing"].get(alert, [])
                    ],
                }
            )
    fixture_path = rules_path.with_name("drill-rule-tests.yml")
    fixture_path.write_text(
        yaml.safe_dump(
            {
                "rule_files": [str(rules_path.resolve())],
                "evaluation_interval": "1m",
                "tests": [
                    {
                        "name": "finite worker evaluator drill",
                        "interval": "30s",
                        "input_series": [],
                        "alert_rule_test": cases,
                    }
                ],
            },
            sort_keys=False,
        )
    )
    return fixture_path


def _production(control_module: Any, config: dict[str, Any], transport: Any) -> str:
    rendered = control_module.render(config)["rules.yml"]
    expected = base64.b64encode(rendered.encode()).decode()
    actual = transport.request("GET", "/extensions/v1/rules/" + control_module.OWNED_RULES)
    if actual.get("content") != expected:
        raise DrillError("production worker rules are not the exact reviewed Git rendering")
    return _hash(rendered)


def _receipt(path: Path) -> dict[str, Any]:
    try:
        receipt = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise DrillError("drill receipt missing or malformed") from error
    if not isinstance(receipt, dict):
        raise DrillError("drill receipt malformed")
    return receipt


def source_run_revision(source: dict[str, Any], repository: str, run_id: str) -> str:
    """Bind recovery to the exact prior protected Monitoring dispatch on main."""
    if not isinstance(source, dict):
        raise DrillError("source Monitoring run identity mismatch")
    sha = source.get("head_sha")
    owner = source.get("repository")
    if (
        not re.fullmatch(r"[0-9]{1,20}", run_id)
        or source.get("id") != int(run_id)
        or source.get("path") != ".github/workflows/monitoring.yml"
        or source.get("event") != "workflow_dispatch"
        or source.get("head_branch") != "main"
        or not isinstance(owner, dict)
        or owner.get("full_name") != repository
        or not isinstance(sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", sha)
    ):
        raise DrillError("source Monitoring run identity mismatch")
    return sha


def _bound(
    control_module: Any,
    config: dict[str, Any],
    transport: Any,
    receipt_path: Path,
    run_id: str,
    revision: str,
) -> tuple[dict[str, Any], str]:
    receipt = _receipt(receipt_path)
    if (
        set(receipt)
        != {
            "version",
            "run_id",
            "revision",
            "workspace_id",
            "folder_id",
            "started_at",
            "expires_at",
            "production_sha256",
            "drill_sha256",
        }
        or receipt.get("version") != 1
        or receipt.get("run_id") != run_id
        or receipt.get("revision") != revision
        or receipt.get("workspace_id") != config["workspace_id"]
        or receipt.get("folder_id") != config["folder_id"]
        or not isinstance(receipt.get("started_at"), int)
        or receipt.get("expires_at") != receipt["started_at"] + EXPIRY_SECONDS
    ):
        raise DrillError("drill receipt identity mismatch")
    content = render_rules(control_module, config, run_id, receipt["started_at"])
    if receipt["drill_sha256"] != _hash(content) or receipt["production_sha256"] != _production(
        control_module, config, transport
    ):
        raise DrillError("drill receipt or production content mismatch")
    return receipt, base64.b64encode(content.encode()).decode()


def prepare_receipt(
    control_module: Any,
    config: dict[str, Any],
    receipt_path: Path,
    run_id: str,
    revision: str,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    control_module.validate_config(config, live=True)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise DrillError("exact main revision required")
    start = int(time.time()) if now is None else now
    content = render_rules(control_module, config, run_id, start)
    rendered_production = control_module.render(config)["rules.yml"]
    receipt = {
        "version": 1,
        "run_id": run_id,
        "revision": revision,
        "workspace_id": config["workspace_id"],
        "folder_id": config["folder_id"],
        "started_at": start,
        "expires_at": start + EXPIRY_SECONDS,
        "production_sha256": _hash(rendered_production),
        "drill_sha256": _hash(content),
    }
    try:
        descriptor = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(receipt, output, sort_keys=True)
            output.write("\n")
    except FileExistsError as error:
        raise DrillError("drill receipt already exists") from error
    return receipt


def start_prepared(
    control_module: Any,
    config: dict[str, Any],
    transport: Any,
    receipt_path: Path,
    run_id: str,
    revision: str,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    receipt, encoded = _bound(control_module, config, transport, receipt_path, run_id, revision)
    observed_at = int(time.time()) if now is None else now
    if not receipt["started_at"] <= observed_at <= receipt["started_at"] + 180:
        raise DrillError("prepared drill receipt start window elapsed")
    files = transport.request("GET", "/extensions/v1/rules").get("files")
    if files != [control_module.OWNED_RULES]:
        raise DrillError("dedicated workspace or temporary rule inventory changed")
    if not transport.request("GET", DRILL_PATH).get("absent"):
        raise DrillError("temporary drill file already exists")
    control_module._preflight_workers(transport, now=observed_at)
    # A failed or uncertain PUT is never retried. The prewritten receipt supports exact cleanup.
    transport.request("PUT", "/extensions/v1/rules", {"name": DRILL_RULES, "content": encoded})
    if transport.request("GET", DRILL_PATH).get("content") != encoded:
        raise DrillError("temporary drill rules read-back mismatch; do not retry PUT")
    if _production(control_module, config, transport) != receipt["production_sha256"]:
        raise DrillError("production rules changed during drill start")
    return receipt


def start_run(
    control_module: Any,
    config: dict[str, Any],
    transport: Any,
    receipt_path: Path,
    run_id: str,
    revision: str,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    prepare_receipt(control_module, config, receipt_path, run_id, revision, now=now)
    return start_prepared(
        control_module, config, transport, receipt_path, run_id, revision, now=now
    )


def cleanup(
    control_module: Any,
    config: dict[str, Any],
    transport: Any,
    receipt_path: Path,
    run_id: str,
    revision: str,
) -> None:
    _, expected = _bound(control_module, config, transport, receipt_path, run_id, revision)
    actual = transport.request("GET", DRILL_PATH)
    if actual.get("absent"):
        return
    if actual.get("content") != expected:
        raise DrillError("temporary drill content mismatch; exact cleanup refused")
    transport.request("DELETE", DRILL_PATH)
    if not transport.request("GET", DRILL_PATH).get("absent"):
        raise DrillError("temporary drill deletion not confirmed")
    _production(control_module, config, transport)


def _scenario(start: int, evaluated_at: int) -> str:
    elapsed = evaluated_at - start
    if elapsed < HEALTHY_END:
        return "healthy"
    if elapsed < SATURATED_END:
        return "saturated"
    if elapsed < MISSING_END:
        return "missing"
    if elapsed < STALE_END:
        return "retained-stale"
    if elapsed < RECOVERED_END:
        return "recovered"
    return "expired"


def status(
    control_module: Any,
    config: dict[str, Any],
    transport: Any,
    receipt_path: Path,
    run_id: str,
    revision: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    receipt, expected = _bound(control_module, config, transport, receipt_path, run_id, revision)
    observed_at = time.time() if now is None else now
    if transport.request("GET", DRILL_PATH).get("content") != expected:
        raise DrillError("temporary drill content mismatch")
    snapshot = transport.request("GET", DRILL_PATH + "/snapshots")
    groups = snapshot.get("snapshotByGroup")
    if not isinstance(groups, dict) or set(groups) != {"findme-worker-activation-drill"}:
        raise DrillError("drill evaluation snapshot missing")
    entries = groups["findme-worker-activation-drill"]
    if not isinstance(entries, list) or len(entries) != len(EXPECTED_ALERTS):
        raise DrillError("drill evaluation snapshot incomplete")
    if {entry.get("record") for entry in entries} != set(EXPECTED_ALERTS):
        raise DrillError("drill evaluation identities changed")
    times = []
    for entry in entries:
        evaluated_ms = entry.get("evaluatedAtTimeEpochMs")
        if (
            entry.get("state") != "OK"
            or entry.get("error")
            or not isinstance(evaluated_ms, (int, float))
        ):
            raise DrillError("drill evaluation failed or is not yet complete")
        evaluated_at = evaluated_ms / 1000
        if (
            not math.isfinite(evaluated_at)
            or not receipt["started_at"] <= evaluated_at <= observed_at + 5
            or observed_at - evaluated_at > EVALUATION_MAX_AGE
        ):
            raise DrillError("drill evaluation stale or outside receipt")
        times.append(evaluated_at)
    if max(times) - min(times) > 90:
        raise DrillError("drill evaluation snapshots are not a single fresh cycle")
    evaluated_at = int(min(times))
    query = 'ALERTS{drill="worker-activation",drill_run=' + json.dumps(run_id) + "}"
    response = transport.request(
        "GET", "/api/v1/query?" + urlencode({"query": query, "time": evaluated_at})
    )
    if (
        response.get("status") != "success"
        or response.get("data", {}).get("resultType") != "vector"
    ):
        raise DrillError("drill ALERTS query failed")
    rows = response["data"].get("result")
    if not isinstance(rows, list):
        raise DrillError("drill ALERTS vector malformed")
    alerts = []
    seen = set()
    for row in rows:
        try:
            labels = row["metric"]
            sample_time, value = row["value"]
            sample_time, value = float(sample_time), float(value)
            key = (labels["alertname"], labels["pool"], labels["alertstate"])
        except (KeyError, TypeError, ValueError):
            raise DrillError("drill ALERTS sample malformed") from None
        if (
            labels.get("drill") != RUN_LABEL
            or labels.get("drill_run") != run_id
            or labels.get("alertname") not in EXPECTED_ALERTS
            or labels.get("pool") not in ("bulk", "selfie")
            or labels.get("alertstate") not in ("pending", "firing")
            or key in seen
            or not math.isfinite(sample_time)
            or not math.isfinite(value)
            or value != 1
            or abs(sample_time - evaluated_at) > 90
        ):
            raise DrillError("drill ALERTS identity or timestamp invalid")
        seen.add(key)
        alerts.append({"alert": key[0], "pool": key[1], "state": key[2], "sample_at": sample_time})
    _production(control_module, config, transport)
    return {
        "run_id": run_id,
        "evaluated_at": evaluated_at,
        "phase": _scenario(receipt["started_at"], evaluated_at),
        "alerts": sorted(alerts, key=lambda item: (item["alert"], item["pool"])),
        "delivery": "unverified",
    }


def audit_observation(start: int, observation: dict[str, Any], seen: set[str]) -> None:
    elapsed = observation["evaluated_at"] - start
    states = {(item["alert"], item["pool"], item["state"]) for item in observation["alerts"]}
    pools = ("bulk", "selfie")
    if 120 <= elapsed < HEALTHY_END and not states:
        seen.add("healthy")
    if (
        HEALTHY_END + 60 <= elapsed < HEALTHY_END + 4 * 60
        and {("WorkerPoolSaturated", pool, "pending") for pool in pools} <= states
    ):
        seen.add("saturation-pending")
    firing = {(alert, pool, "firing") for alert in EXPECTED_ALERTS[:2] for pool in pools}
    if HEALTHY_END + 6 * 60 <= elapsed < SATURATED_END and states == firing:
        seen.add("saturation-firing")
    if SATURATED_END + 2 * 60 <= elapsed < MISSING_END:
        expected = {(alert, "bulk", "firing") for alert in EXPECTED_ALERTS[2:5]} | {
            (alert, "selfie", "firing") for alert in EXPECTED_ALERTS[5:]
        }
        if states == expected:
            seen.add("source-missing")
    if MISSING_END + 2 * 60 <= elapsed < STALE_END:
        expected = {(alert, "selfie", "firing") for alert in EXPECTED_ALERTS[2:5]} | {
            (alert, "bulk", "firing") for alert in EXPECTED_ALERTS[5:]
        }
        if states == expected:
            seen.add("retained-stale")
    if STALE_END + 2 * 60 <= elapsed < RECOVERED_END and not states:
        seen.add("recovered")


def run(
    control_module: Any,
    config: dict[str, Any],
    transport: Any,
    receipt_path: Path,
    report_path: Path,
    run_id: str,
    revision: str,
) -> None:
    receipt = start_prepared(control_module, config, transport, receipt_path, run_id, revision)
    start = receipt["started_at"]
    seen: set[str] = set()
    observations: list[dict[str, Any]] = []
    cleanup_result = "unconfirmed"
    try:
        while time.time() < start + RUN_END:
            try:
                observed = status(control_module, config, transport, receipt_path, run_id, revision)
            except DrillError as error:
                if time.time() < start + EVALUATION_MAX_AGE and str(error) in {
                    "drill evaluation snapshot missing",
                    "drill evaluation snapshot incomplete",
                    "drill evaluation failed or is not yet complete",
                }:
                    time.sleep(POLL_SECONDS)
                    continue
                raise
            if not observations or observations[-1]["evaluated_at"] != observed["evaluated_at"]:
                observations.append(observed)
                audit_observation(start, observed, seen)
            if seen == REQUIRED_EVIDENCE:
                break
            time.sleep(POLL_SECONDS)
        if seen != REQUIRED_EVIDENCE:
            raise DrillError("bounded drill ended without all evaluator states")
    finally:
        try:
            cleanup(control_module, config, transport, receipt_path, run_id, revision)
            cleanup_result = "confirmed"
        finally:
            report_path.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "revision": revision,
                        "seen": sorted(seen),
                        "observations": observations,
                        "cleanup": cleanup_result,
                        "delivery": "unverified until recipient receipt",
                    },
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("render", "prepare", "run", "status", "cleanup", "source-revision")
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--revision")
    parser.add_argument("--repository")
    parser.add_argument("--identity", choices=("yc", "github-oidc"), default="yc")
    parser.add_argument("--oidc-config", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "source-revision":
            if not args.repository or not args.run_id:
                raise DrillError("source run requires repository and run ID")
            print(source_run_revision(json.load(sys.stdin), args.repository, args.run_id))
            return 0
        config = control.load_config(args.config)
        if args.command == "render":
            if args.output is None:
                raise DrillError("render requires --output")
            args.output.mkdir(parents=True, exist_ok=True)
            rules = render_rules(control, config, "123456", 0)
            path = args.output / DRILL_RULES
            path.write_text(rules)
            write_promtool_fixture(path)
        else:
            if not args.receipt or not args.run_id or not args.revision:
                raise DrillError("live drill requires receipt, run ID and exact revision")
            if args.command == "prepare":
                prepare_receipt(control, config, args.receipt, args.run_id, args.revision)
                return 0
            transport = control.CloudTransport(
                config, control.identity(args.identity, args.oidc_config)
            )
            if args.command == "run":
                if args.report is None:
                    raise DrillError("run requires --report")
                run(
                    control,
                    config,
                    transport,
                    args.receipt,
                    args.report,
                    args.run_id,
                    args.revision,
                )
            elif args.command == "cleanup":
                cleanup(control, config, transport, args.receipt, args.run_id, args.revision)
            else:
                print(
                    json.dumps(
                        status(
                            control, config, transport, args.receipt, args.run_id, args.revision
                        ),
                        sort_keys=True,
                    )
                )
    except (
        control.ControlError,
        DrillError,
        OSError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        print(
            str(error) if isinstance(error, DrillError) else "worker drill failed; inspect receipt",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
