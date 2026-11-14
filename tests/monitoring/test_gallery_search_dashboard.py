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


def test_queue_queries_show_durations_and_changing_shares_for_the_same_cohort(tmp_path):
    dashboard = json.loads(
        (ROOT / "deploy/monitoring/prometheus/single-vm-dashboard.json").read_text()
    )
    series = []
    expressions = []
    idle_expressions = []
    for queue, segments in (
        ("gallery_search", {"matching_wait": 5, "matching_elapsed": 15}),
        (
            "selfie_search",
            {
                "recognition_wait": 3,
                "recognition_elapsed": 9,
                "matching_wait": 6,
                "matching_elapsed": 12,
            },
        ),
    ):
        by_visualization = {
            (
                chart["visualizationSettings"]["type"],
                chart["visualizationSettings"]
                .get("yaxisSettings", {})
                .get("left", {})
                .get("title"),
            ): target["prometheusTarget"]["query"]
            for chart in charts(dashboard["widgets"])
            for target in chart["targets"]
            if f'queue="{queue}"' in target["prometheusTarget"]["query"]
            and "findme_queue_segment_duration_seconds_total" in target["prometheusTarget"]["query"]
        }
        stack = by_visualization["VISUALIZATION_TYPE_STACK", "Секунды"]
        share = by_visualization["VISUALIZATION_TYPE_STACK", "%"]
        pie = by_visualization["VISUALIZATION_TYPE_PIE", None]
        labels = f'job="findme-queue-telemetry",queue="{queue}"'
        series.append(
            {"series": f"findme_queue_completed_total{{{labels}}}", "values": "0+1x10 10+0x5"}
        )
        later_segments = {
            name: value * (2 if name.endswith("_wait") else 1) for name, value in segments.items()
        }
        for segment, seconds in segments.items():
            later_seconds = later_segments[segment]
            series.append(
                {
                    "series": (
                        f'findme_queue_segment_duration_seconds_total{{{labels},segment="{segment}"}}'
                    ),
                    "values": (
                        f"0+{seconds}x5 "
                        f"{5 * seconds + later_seconds}+{later_seconds}x4 "
                        f"{5 * (seconds + later_seconds)}+0x5"
                    ),
                }
            )
        expected = [
            {"labels": f'{{segment="{segment}"}}', "value": value}
            for segment, value in segments.items()
        ]
        expressions.extend(
            [
                {"expr": stack, "eval_time": "5m", "exp_samples": expected},
                {
                    "expr": f"sum({stack})",
                    "eval_time": "5m",
                    "exp_samples": [{"labels": "{}", "value": sum(segments.values())}],
                },
                {
                    "expr": f"sum({pie})",
                    "eval_time": "5m",
                    "exp_samples": [{"labels": "{}", "value": sum(segments.values()) * 5}],
                },
            ]
        )
        for minute, window in ((5, segments), (10, later_segments)):
            expressions.extend(
                [
                    {
                        "expr": share,
                        "eval_time": f"{minute}m",
                        "exp_samples": [
                            {
                                "labels": f'{{segment="{segment}"}}',
                                "value": 100 * seconds / sum(window.values()),
                            }
                            for segment, seconds in window.items()
                        ],
                    },
                    {
                        "expr": f"sum({share})",
                        "eval_time": f"{minute}m",
                        "exp_samples": [{"labels": "{}", "value": 100}],
                    },
                ]
            )
        idle_expressions.extend(
            [
                {"expr": stack, "eval_time": "15m", "exp_samples": []},
                {"expr": share, "eval_time": "15m", "exp_samples": []},
                {
                    "expr": f"sum({pie})",
                    "eval_time": "15m",
                    "exp_samples": [{"labels": "{}", "value": 0}],
                },
            ]
        )

    tests = {
        "evaluation_interval": "1m",
        "fuzzy_compare": True,
        "tests": [
            {
                "interval": "1m",
                "input_series": series,
                "promql_expr_test": expressions + idle_expressions,
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
