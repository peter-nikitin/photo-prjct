"""Durable queue progress, threshold and freshness behaviour through real PromQL."""

import os
import subprocess

import yaml
from config.queue_catalog import QUEUES

from tests.monitoring.test_alert_reconciliation import load


def test_stalled_queue_is_independent_and_recovers_with_own_progress(tmp_path):
    (tmp_path / "rules.yml").write_bytes(load().render_rules())
    queues = {q.key: q for q in QUEUES}

    def series(key, success="0+0x30", observed="0+60x30", age="1800+60x30", count="1+0x30"):
        labels = f'queue="{key}",instance="findme-photo-app"'
        return [
            {"series": f"findme_queue_{metric}{{{labels}}}", "values": values}
            for metric, values in (
                ("due_count", count),
                ("oldest_due_age_seconds", age),
                ("last_success_timestamp_seconds", success),
                ("due_observed_timestamp_seconds", observed),
            )
        ]

    def expected(key, threshold):
        title = queues[key].title
        return {
            "exp_labels": {
                "queue": key,
                "queue_title": title,
                "stall_threshold": str(threshold),
                "instance": "findme-photo-app",
                "project": "findme-photo",
                "notification": "actionable",
                "severity": "critical",
                "incident": "queue",
            },
            "exp_annotations": {
                "summary": f"Очередь „{title}“ ({key}) не продвигается",
                "description": "Готовая работа и последнее успешное завершение старше "
                f"{threshold} секунд. "
                "Состояние сохраняется не менее 5 минут.",
            },
        }

    def scenario(inputs, checks):
        return {
            "interval": "1m",
            "input_series": inputs,
            "alert_rule_test": [
                {"eval_time": at, "alertname": "ReadyWorkStalled", "exp_alerts": result}
                for at, result in checks
            ],
        }

    bib = "photo_bib_recognition"
    tests = [
        scenario(
            series(bib) + series("photo_face_embedding", success="0+60x30"),
            [("21m", [expected(bib, 900)])],
        ),
        scenario(
            series(bib, success="0+0x21 1320+60x8"),
            [("19m", []), ("21m", [expected(bib, 900)]), ("22m", [])],
        ),
        scenario(
            series("commerce_email") + series("commerce_payment", success="0+60x30"),
            [("21m", [expected("commerce_email", 900)])],
        ),
        scenario(
            series("selfie_matching"), [("10m", []), ("11m", [expected("selfie_matching", 300)])]
        ),
        scenario(series(bib, observed="0+0x30"), [("25m", [])]),
        scenario(series(bib, observed="6000+0x30"), [("25m", [])]),
        scenario(series(bib, count="0+0x30"), [("25m", [])]),
        scenario(series(bib, age="900+0x30"), [("25m", [])]),
        scenario(series(bib)[:-1], [("25m", [])]),
    ]
    fixture = {"rule_files": ["rules.yml"], "evaluation_interval": "1m", "tests": tests}
    (tmp_path / "fixtures.yml").write_text(yaml.safe_dump(fixture, allow_unicode=True))
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
            "/data/fixtures.yml",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
