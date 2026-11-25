"""Execute generated instruments for queue isolation, timing cohorts and source loss."""

import importlib.util
from dataclasses import replace
from pathlib import Path

from config.queue_catalog import QUEUES

from tests.monitoring.test_gallery_search_dashboard import _test_promql

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location(
        "queue_panels", ROOT / "deploy/monitoring/prometheus/queue_panels.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def query(panel, title, target=0):
    return next(
        widget["multiSourceChart"]["targets"][target]["prometheusTarget"]["query"]
        for widget in panel["group"]["widgets"]
        if widget["multiSourceChart"]["title"].startswith(title)
    )


def test_catalog_addition_gets_same_instruments_without_changing_existing_identity():
    module = load()
    original = module.panels(QUEUES, "workspace")
    added = replace(QUEUES[0], key="new_work", title="Новая работа")
    extended = module.panels((*QUEUES, added), "workspace")
    assert extended[:-1] == original
    template = original[0]["group"]["widgets"]
    new_widgets = extended[-1]["group"]["widgets"]
    assert len(template) == len(new_widgets) == 7
    assert [w["position"] for w in template] == [w["position"] for w in new_widgets]
    assert [w["multiSourceChart"]["title"] for w in template] == [
        w["multiSourceChart"]["title"] for w in new_widgets
    ]
    assert {w["id"] for w in template}.isdisjoint(w["id"] for w in new_widgets)
    for w in new_widgets:
        for target in w["multiSourceChart"]["targets"]:
            expression = target["prometheusTarget"]["query"]
            assert 'queue="new_work"' in expression or "listener_connected" in expression


def test_generated_instruments_keep_states_distinct_and_hide_unavailable_or_stale(tmp_path):
    panel = load().panels(QUEUES[:1], "workspace")[0]
    key = QUEUES[0].key
    work = f'job="findme-queue-telemetry",queue="{key}"'
    ready = f'job="findme-http",queue="{key}"'
    series = [
        {"series": f'findme_queue_work_count{{{work},state="waiting"}}', "values": "9+0x12"},
        {"series": f'findme_queue_work_count{{{work},state="processing"}}', "values": "2+0x12"},
        {"series": f"findme_queue_work_available{{{work}}}", "values": "1+0x5 0 1+0x5"},
        {
            "series": f"findme_queue_work_observed_timestamp_seconds{{{work}}}",
            "values": "0+60x8 480+0x4",
        },
        {"series": f"findme_queue_ready_count{{{ready}}}", "values": "0+0x12"},
        {
            "series": f"findme_queue_oldest_ready_age_seconds{{{ready}}}",
            "values": "0+0x12",
        },
        {
            "series": f"findme_queue_ready_observed_timestamp_seconds{{{ready}}}",
            "values": "0+60x8 480+0x4",
        },
    ]
    expressions = [
        {
            "expr": query(panel, "Незавершённые", 0),
            "eval_time": "5m",
            "exp_samples": [{"labels": "{}", "value": 9}],
        },
        {
            "expr": query(panel, "Незавершённые", 1),
            "eval_time": "5m",
            "exp_samples": [{"labels": "{}", "value": 2}],
        },
        {"expr": query(panel, "Незавершённые"), "eval_time": "6m", "exp_samples": []},
        {"expr": query(panel, "Незавершённые"), "eval_time": "12m", "exp_samples": []},
        {
            "expr": query(panel, "Готово"),
            "eval_time": "5m",
            "exp_samples": [{"labels": "{}", "value": 0}],
        },
        {
            "expr": query(panel, "Возраст"),
            "eval_time": "5m",
            "exp_samples": [{"labels": "{}", "value": 0}],
        },
        {"expr": query(panel, "Готово"), "eval_time": "12m", "exp_samples": []},
    ]
    _test_promql(tmp_path, series, expressions)


def test_segment_means_use_measured_cohort_and_pie_accumulates_reset_safe_scrape_deltas(tmp_path):
    panel = load().panels(QUEUES[:1], "workspace")[0]
    key = QUEUES[0].key
    segment = QUEUES[0].segments[0].key
    labels = f'job="findme-queue-telemetry",queue="{key}"'
    series = [
        {"series": f'findme_queue_results_total{{{labels},outcome="success"}}', "values": "0+2x12"},
        {"series": f"findme_queue_duration_observations_total{{{labels}}}", "values": "0+1x12"},
        {
            "series": (
                f'findme_queue_segment_duration_seconds_total{{{labels},segment="{segment}"}}'
            ),
            "values": "0 10 20 30 40 _ 60 10 20 20+0x3",
        },
        {
            "series": f"findme_queue_results_observed_timestamp_seconds{{{labels}}}",
            "values": "0+60x8 480+0x4",
        },
        {
            "series": 'findme_queue_results_listener_connected{job="findme-queue-telemetry"}',
            "values": "1+0x12",
        },
        {
            "series": (
                'findme_queue_segment_duration_seconds_total{job="findme-queue-telemetry",'
                'queue="other_work",segment="wait"}'
            ),
            "values": "0+1000x12",
        },
    ]
    # promtool expressions are instant queries, whose start() equals their end.
    # Bind the dashboard's range start explicitly for these point evaluations.
    pie = query(panel, "Доли времени").replace("@ start()", "@ 0")
    expressions = [
        {
            "expr": query(panel, "Среднее время"),
            "eval_time": "4m",
            "exp_samples": [{"labels": "{}", "value": 10}],
        },
        {
            "expr": query(panel, "Число завершений").replace("@ start()", "@ 0"),
            "eval_time": "6m",
            "exp_samples": [{"labels": "{}", "value": 2}],
        },
        {"expr": pie, "eval_time": "4m", "exp_samples": [{"labels": "{}", "value": 10}]},
        {"expr": pie, "eval_time": "5m", "exp_samples": []},
        {"expr": pie, "eval_time": "6m", "exp_samples": [{"labels": "{}", "value": 20}]},
        {"expr": pie, "eval_time": "7m", "exp_samples": [{"labels": "{}", "value": 10}]},
        {"expr": pie, "eval_time": "9m", "exp_samples": []},
        {"expr": pie, "eval_time": "0m", "exp_samples": []},
        {
            "expr": f"sum_over_time(({pie.replace('@ 0', '@ 300')})[4m:1m])",
            "eval_time": "9m",
            "exp_samples": [{"labels": "{}", "value": 40}],
        },
        {"expr": query(panel, "Среднее время"), "eval_time": "12m", "exp_samples": []},
    ]
    _test_promql(tmp_path, series, expressions)


def test_pie_keeps_both_jittered_scrapes_and_the_peak_before_a_reset(tmp_path):
    module = load()
    panel = module.panels(QUEUES[:1], "workspace")[0]
    queue = QUEUES[0]
    labels = f'job="findme-queue-telemetry",queue="{queue.key}"'
    series = [
        {
            "series": f"findme_queue_results_observed_timestamp_seconds{{{labels}}}",
            "values": "0+1x180",
        },
        {
            "series": 'findme_queue_results_listener_connected{job="findme-queue-telemetry"}',
            "values": "1+0x180",
        },
    ]
    expressions = []
    # Actual scrapes at 0, 61, 119 seconds, chart evaluations at 0, 60, 120.
    # The second case resets between the two new observations; the third resets
    # before the first, then grows beyond the previous snapshot in the same bucket.
    # The cold-start pair has no earlier sample and contributes only its observed change.
    for index, (initial, first, last, expected) in enumerate(
        ((0, 10, 30, 30), (0, 10, 3, 13), (100, 5, 110, 110), ("_", 10, 30, 20))
    ):
        segment = queue.segments[index]
        series.append(
            {
                "series": (
                    f"findme_queue_segment_duration_seconds_total{{{labels},"
                    f'segment="{segment.key}"}}'
                ),
                "values": f"{initial} _x60 {first} _x57 {last} _x60",
            }
        )
        expression = query(panel, "Доли времени", index).replace("@ start()", "@ 0")
        expressions += [
            {"expr": expression, "eval_time": "60s", "exp_samples": []},
            {
                "expr": expression,
                "eval_time": "120s",
                "exp_samples": [{"labels": "{}", "value": expected}],
            },
        ]
    _test_promql(tmp_path, series, expressions, interval="1s")


def test_all_queue_targets_fit_a_monium_get_request():
    from urllib.parse import quote, urlencode

    path = "/monitoring/v2/prometheus/workspaces/workspace01234567890/api/v1/query_range"
    for panel in load().panels(QUEUES, "workspace"):
        targets = (
            target
            for widget in panel["group"]["widgets"]
            for target in widget["multiSourceChart"]["targets"]
        )
        for target in targets:
            encoded = urlencode(
                {
                    "query": target["prometheusTarget"]["query"],
                    "start": "2026-11-22T14:00:00.000Z",
                    "end": "2026-11-22T15:00:00.000Z",
                    "step": 60,
                },
                quote_via=quote,
            )
            request_line = f"GET {path}?{encoded} HTTP/1.1\r\n"
            # Leave room for the dashboard proxy's request framing.
            assert len(request_line.encode()) <= 4096, panel["group"]["title"]


def test_results_count_observed_minute_increments_by_outcome(tmp_path):
    panel = load().panels(QUEUES[:1], "workspace")[0]
    labels = f'job="findme-queue-telemetry",queue="{QUEUES[0].key}"'
    series = [
        {
            "series": f"findme_queue_results_observed_timestamp_seconds{{{labels}}}",
            "values": "0+1x180 180+0x181",
        },
        {
            "series": 'findme_queue_results_listener_connected{job="findme-queue-telemetry"}',
            "values": "1+0x179 0+0x59 1+0x121",
        },
    ]
    expressions = []
    for index, (outcome, first, last, expected) in enumerate(
        (("success", 10, 30, 30), ("failed", 10, 3, 13), ("canceled", 0, 0, 0))
    ):
        series.append(
            {
                "series": f'findme_queue_results_total{{{labels},outcome="{outcome}"}}',
                "values": f"0 _x60 {first} _x57 {last} _x60 {last} _x59 {last} _x119 {last}",
            }
        )
        expression = query(panel, "Число завершений", index).replace("@ start()", "@ 0")
        expressions += [
            {"expr": expression, "eval_time": "0s", "exp_samples": []},
            {"expr": expression, "eval_time": "60s", "exp_samples": []},
            {
                "expr": expression,
                "eval_time": "120s",
                "exp_samples": [{"labels": "{}", "value": expected}],
            },
            {"expr": expression, "eval_time": "180s", "exp_samples": []},
            {
                "expr": expression,
                "eval_time": "240s",
                "exp_samples": [{"labels": "{}", "value": 0}],
            },
            {"expr": expression, "eval_time": "360s", "exp_samples": []},
        ]
    _test_promql(tmp_path, series, expressions, interval="1s")


def test_full_cycle_percentiles_hide_empty_windows_and_missing_sources(tmp_path):
    panel = load().panels(QUEUES[:1], "workspace")[0]
    labels = f'job="findme-queue-telemetry",queue="{QUEUES[0].key}"'
    metric = "findme_queue_cycle_duration_seconds"
    counts = "0 0 100+0x4 200+0x5"
    series = [
        {"series": f'{metric}_bucket{{{labels},le="10.0"}}', "values": "0+0x12"},
        {"series": f'{metric}_bucket{{{labels},le="60.0"}}', "values": counts},
        {"series": f'{metric}_bucket{{{labels},le="+Inf"}}', "values": counts},
        {"series": f"{metric}_count{{{labels}}}", "values": counts},
        {
            "series": f"findme_queue_results_observed_timestamp_seconds{{{labels}}}",
            "values": "0+60x5 300+0x6",
        },
        {
            "series": 'findme_queue_results_listener_connected{job="findme-queue-telemetry"}',
            "values": "1+0x3 0 1+0x6",
        },
    ]
    expressions = []
    for target, percentile in enumerate((35, 57.5, 59.5)):
        expression = query(panel, "Перцентили", target)
        expressions += [
            {"expr": expression, "eval_time": "1m", "exp_samples": []},
            {
                "expr": expression,
                "eval_time": "3m",
                "exp_samples": [{"labels": "{}", "value": percentile}],
            },
            {"expr": expression, "eval_time": "4m", "exp_samples": []},
            {"expr": expression, "eval_time": "6m", "exp_samples": []},
            {"expr": expression, "eval_time": "8m", "exp_samples": []},
        ]
    _test_promql(tmp_path, series, expressions)
