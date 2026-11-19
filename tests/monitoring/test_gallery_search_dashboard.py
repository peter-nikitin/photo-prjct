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
            "photo_import_gallery",
            {"preview_wait": 5, "preview_elapsed": 15, "watermark_wait": 2, "watermark_elapsed": 4},
        ),
        (
            "photo_import_face",
            {"preview_wait": 5, "preview_elapsed": 15, "face_wait": 3, "face_elapsed": 7},
        ),
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
            if "prometheusTarget" in target
            and f'queue="{queue}"' in target["prometheusTarget"]["query"]
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
                        "expr": f"round({share}, 0.000001)",
                        "eval_time": f"{minute}m",
                        "exp_samples": [
                            {
                                "labels": f'{{segment="{segment}"}}',
                                "value": round(100 * seconds / sum(window.values()), 6),
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

    for queue, initial, failures in (("photo_import_gallery", 12, 3), ("photo_import_face", 8, 2)):
        for metric, status, first, later in (
            ("pending", "Осталось", initial, initial - 2),
            ("failed", "Ошибки", failures, failures - 1),
        ):
            metric_name = f"findme_photo_import_{metric}_count"
            labels = f'job="findme-queue-telemetry",queue="{queue}"'
            series.append({"series": f"{metric_name}{{{labels}}}", "values": f"{first} {later} 0"})
            (query,) = (
                target["prometheusTarget"]["query"]
                for chart in charts(dashboard["widgets"])
                for target in chart["targets"]
                if "prometheusTarget" in target
                and metric_name in target["prometheusTarget"]["query"]
                and f'queue="{queue}"' in target["prometheusTarget"]["query"]
            )
            expressions.extend(
                {
                    "expr": query,
                    "eval_time": moment,
                    "exp_samples": [
                        {"labels": f'{metric_name}{{{labels},status="{status}"}}', "value": value}
                    ],
                }
                for moment, value in (("0m", first), ("1m", later), ("2m", 0))
            )
            idle_expressions.append({"expr": query, "eval_time": "15m", "exp_samples": []})

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
