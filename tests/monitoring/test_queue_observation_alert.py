"""Run the published alert against complete, interrupted, and missing sources."""

import os
import subprocess
from dataclasses import replace

import yaml
from config.queue_catalog import QUEUES

from tests.monitoring.test_alert_reconciliation import load

INSTANCE = 'instance="findme-photo-app"'
TELEMETRY = f'{INSTANCE},job="findme-queue-telemetry"'
HTTP = f'{INSTANCE},job="findme-http"'
ALERT = "QueueObservationUnavailable"


def _series(queues, overrides=None, listener="1+0x13"):
    overrides = overrides or {}
    result = [
        {"series": f"findme_queue_results_listener_connected{{{TELEMETRY}}}", "values": listener}
    ]
    for queue in queues:
        for metric, job, default in (
            ("work_available", TELEMETRY, "1+0x13"),
            ("work_observed_timestamp_seconds", TELEMETRY, "0+60x13"),
            ("ready_observed_timestamp_seconds", HTTP, "0+60x13"),
            ("results_observed_timestamp_seconds", TELEMETRY, "0+60x13"),
            ("ready_count", HTTP, "0+0x13"),
        ):
            values = overrides.get((queue.key, metric), default)
            if values is not None:
                result.append(
                    {
                        "series": f'findme_queue_{metric}{{{job},queue="{queue.key}"}}',
                        "values": values,
                    }
                )
    return result


def _expected(queue, source):
    return {
        "exp_labels": {
            "queue": queue,
            "source": source,
            "instance": "findme-photo-app",
            "severity": "critical",
            "project": "findme-photo",
            "notification": "actionable",
            "incident": "queue-observation",
        },
        "exp_annotations": {
            "summary": "Недоступен источник наблюдения очереди",
            "description": f"Очередь {queue}, источник {source}. "
            "Состояние сохраняется не менее 5 минут.",
        },
    }


def test_rendered_alert_handles_partial_and_complete_source_loss(tmp_path):
    module = load()
    added = replace(QUEUES[0], key="new_work")
    queues = (*QUEUES, added)
    key = QUEUES[0].key
    (tmp_path / "rules.yml").write_bytes(module.render_rules(queues))

    def scenario(series, *expectations):
        return {
            "interval": "1m",
            "input_series": series,
            "alert_rule_test": [
                {"eval_time": at, "alertname": ALERT, "exp_alerts": alerts}
                for at, alerts in expectations
            ],
        }

    failures = {
        (key, "work_available"): "1 0+0x7 1+0x5",
        (key, "ready_observed_timestamp_seconds"): "0+0x9 600+60x3",
        (added.key, "results_observed_timestamp_seconds"): None,
    }
    work, ready, new_results = (
        _expected(key, "work"),
        _expected(key, "ready"),
        _expected(added.key, "results"),
    )
    disconnected = [_expected(queue.key, "results") for queue in queues]
    absent = [
        _expected(queue.key, source) for queue in queues for source in ("work", "ready", "results")
    ]

    interrupted = _series(queues, failures)
    metric = "findme_queue_results_observed_timestamp_seconds"
    for labels in (HTTP, TELEMETRY.replace("findme-photo-app", "other-instance")):
        interrupted.append(
            {
                "series": f'{metric}{{{labels},queue="{added.key}"}}',
                "values": "0+60x13",
            }
        )
    tests = {
        "rule_files": ["rules.yml"],
        "evaluation_interval": "1m",
        "tests": [
            scenario(_series(queues), ("12m", [])),
            scenario(
                interrupted,
                ("4m", []),
                ("5m", [new_results]),
                ("6m", [new_results, work]),
                ("8m", [new_results, work, ready]),
                ("11m", [new_results]),
            ),
            scenario(
                _series(queues, listener="1 0+0x2 1+0x10"),
                ("4m", []),
                ("12m", []),
            ),
            scenario(
                _series(queues, listener="0+0x7 1+0x5"),
                ("4m", []),
                ("6m", disconnected),
                ("9m", []),
            ),
            scenario([], ("4m", []), ("5m", absent)),
        ],
    }
    (tmp_path / "fixtures.yml").write_text(yaml.safe_dump(tests))
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
