"""Model-free, fail-closed comparison of one sealed local production cohort."""

from __future__ import annotations

import json
import math
from pathlib import Path

MIB = 1024**2
STAGES = (
    "download",
    "ocr",
    "visual",
    "validation",
    "total",
    "capture_metadata",
    "generate_preview",
    "face_embedding",
)
METRICS = (
    "peak_rss_bytes",
    "memory_limit_bytes",
    "worker_cpus",
    "replicas",
    "oom_kills",
    "restarts",
    "lease_expired",
    "host_mem_available_min_bytes",
    "swap_in_pages",
    "swap_out_pages",
    "cpu_saturation_seconds",
    "iowait_saturation_seconds",
    "health_failures",
    "baseline_web_p95_ms",
    "web_p95_ms",
    "wall_seconds",
    "peak_cpu_percent",
    "peak_iowait_percent",
    "disk_available_min_bytes",
    "lease_max_seconds",
    "sample_count",
)


def write_immutable(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def compare(baseline: dict, evidence: dict) -> dict:
    failures: list[str] = []
    changed = []
    selected = None
    try:
        rows = evidence["rows"]
        expected = {p["relative_path"]: p["sha256"] for p in baseline["corpus"]["photos"]}
        if (
            not expected
            or len(expected) != len(rows)
            or {r["relative_path"]: r["sha256"] for r in rows} != expected
            or len({r["photo_id"] for r in rows}) != len(rows)
        ):
            failures.append("cohort_mismatch")
        actual = set()
        by_number: dict[str, set[str]] = {}
        for row in rows:
            if row["event_slug"] != evidence["event_slug"]:
                failures.append("event_leak")
            if row["published"] is not True or row["terminal"] is not True:
                failures.append("publication_or_terminal_regression")
            for number in row["numbers"]:
                if (
                    not isinstance(number, str)
                    or not number.isascii()
                    or not number.isdigit()
                    or not 1 <= len(number) <= 16
                ):
                    raise ValueError("invalid number")
                actual.add((row["relative_path"], number))
                by_number.setdefault(number, set()).add(row["photo_id"])
        excluded = {(r["relative_path"], r["number"]) for r in baseline["excluded"]}
        confirmed = set(map(tuple, baseline["confirmed"])) - excluded
        rejected = set(map(tuple, baseline["rejected"]))
        if confirmed - actual:
            failures.append("confirmed_miss")
        if rejected & actual:
            failures.append("rejected_junk")
        changed = sorted(
            (actual ^ set(map(tuple, baseline["baseline_accepted"])))
            - confirmed
            - rejected
            - excluded
        )
        if changed:
            failures.append("unreviewed_changes_require_manual_inspection")
        queries = {n for _, n in actual | confirmed | rejected}
        queries |= {str(int(n)) if n.startswith("0") else "0" + n for n in queries if len(n) < 16}
        for number in queries:
            returned = evidence["search"][number]
            if len(returned) != len(set(returned)) or set(returned) != by_number.get(number, set()):
                failures.append("exact_search_or_leading_zero_regression")
        if evidence["isolation_passed"] is not True:
            failures.append("event_isolation_failed")
        m = evidence["metrics"]
        for key in METRICS:
            value = m[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("invalid metric")
        for stage in STAGES:
            values = evidence["stages"][stage]
            if (
                values["count"] < 1
                or not 0 <= values["p50"] <= values["p95"]
                or not math.isfinite(values["p95"])
            ):
                raise ValueError("invalid stage")
        selected = max(2048, math.ceil(m["peak_rss_bytes"] / 0.70 / (512 * MIB)) * 512)
        if (
            selected > 6144
            or m["peak_rss_bytes"] > m["memory_limit_bytes"] * 0.70
            or not 2048 * MIB <= m["memory_limit_bytes"] <= 6144 * MIB
        ):
            failures.append("memory_headroom")
        if m["replicas"] != 1 or m["worker_cpus"] != 2:
            failures.append("worker_resources")
        if any(
            m[k]
            for k in (
                "oom_kills",
                "restarts",
                "lease_expired",
                "swap_in_pages",
                "swap_out_pages",
                "health_failures",
            )
        ):
            failures.append("runtime_failure")
        if m["host_mem_available_min_bytes"] < 1024 * MIB:
            failures.append("host_headroom")
        if m["cpu_saturation_seconds"] > 300 or m["iowait_saturation_seconds"] > 300:
            failures.append("host_saturation")
        if m["baseline_web_p95_ms"] <= 0 or m["web_p95_ms"] > 2 * m["baseline_web_p95_ms"]:
            failures.append("web_latency")
        if m["sample_count"] < 2 or m["wall_seconds"] <= 0 or m["peak_rss_bytes"] <= 0:
            failures.append("missing_measurement")
    except (KeyError, TypeError, ValueError, OverflowError):
        failures.append("malformed_or_missing_evidence")
    return {
        "schema_version": 1,
        "status": "RED" if failures else "GREEN",
        "failures": sorted(set(failures)),
        "changed_unreviewed_pairs": changed,
        "selected_memory_mib": selected,
        "selected_worker_cpus": 2,
        "replicas": 1,
    }
