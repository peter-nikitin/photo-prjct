"""Execute the shipped dashboard expressions against counters, not JSON snapshots."""

import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def charts(widgets):
    for widget in widgets:
        if "group" in widget:
            yield from charts(widget["group"]["widgets"])
        elif "multiSourceChart" in widget:
            yield widget["multiSourceChart"]


def test_phase_queries_preserve_end_to_end_sum_and_no_completions_are_missing(tmp_path):
    dashboard = json.loads(
        (ROOT / "deploy/monitoring/prometheus/single-vm-dashboard.json").read_text()
    )
    queries = [
        target["prometheusTarget"]["query"]
        for chart in charts(dashboard["widgets"])
        for target in chart["targets"]
        if "findme_gallery_search_phase_duration_seconds_total"
        in target["prometheusTarget"]["query"]
    ]
    queue, processing = queries
    expressions = [
        {"expr": queue, "eval_time": "5m", "exp_samples": [{"labels": "{}", "value": 5}]},
        {"expr": processing, "eval_time": "5m", "exp_samples": [{"labels": "{}", "value": 15}]},
        {
            "expr": f"({queue}) + ({processing})",
            "eval_time": "5m",
            "exp_samples": [{"labels": "{}", "value": 20}],
        },
    ]
    series = [
        {"series": 'findme_gallery_search_completed_total{job="findme-http"}', "values": "0+1x5"},
        {
            "series": "findme_gallery_search_phase_duration_seconds_total"
            '{job="findme-http",phase="queue"}',
            "values": "0+5x5",
        },
        {
            "series": "findme_gallery_search_phase_duration_seconds_total"
            '{job="findme-http",phase="processing"}',
            "values": "0+15x5",
        },
    ]
    tests = {
        "evaluation_interval": "1m",
        "tests": [
            {"interval": "1m", "input_series": series, "promql_expr_test": expressions},
            {
                "interval": "1m",
                "input_series": [{**item, "values": "0+0x5"} for item in series],
                "promql_expr_test": [{**item, "exp_samples": []} for item in expressions],
            },
        ],
    }
    (tmp_path / "gallery.yml").write_text(yaml.safe_dump(tests))
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            "none",
            "--entrypoint",
            "/bin/promtool",
            "-v",
            f"{tmp_path}:/data:ro",
            "prom/prometheus:v3.5.0",
            "test",
            "rules",
            "/data/gallery.yml",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
