import json
from unittest.mock import patch

import pytest
from django.db import DatabaseError
from selfie_search.services import apparel_telemetry as telemetry


def test_success_reports_remaining_shared_budget():
    with patch.object(telemetry, "perf_counter", side_effect=[1, 1.25]):
        with patch.object(telemetry.logger, "info") as log:
            with telemetry.stage("expansion", "search", 1500, 1200):
                pass
    payload = json.loads(log.call_args.args[0])
    assert payload["elapsed_ms"] == 250
    assert payload["budget_remaining_ms"] == 1250
    assert payload["statement_timeout_ms"] == 1200
    assert payload["reason"] == "succeeded"


@pytest.mark.parametrize(
    "code,reason",
    [("57014", "statement_timeout"), ("55P03", "lock_timeout"), ("XX000", "database_error")],
)
def test_database_failure_keeps_exception_and_hides_details(code, reason):
    class Cause(Exception):
        sqlstate = code

    error = DatabaseError("private SQL parameters")
    error.__cause__ = Cause()
    with patch.object(telemetry.logger, "info") as log:
        with pytest.raises(DatabaseError) as raised:
            with telemetry.stage("anchor_verification", "search", 2000, 2000):
                raise error
    assert raised.value is error
    payload = json.loads(log.call_args.args[0])
    assert payload["reason"] == reason
    assert "private" not in log.call_args.args[0]


def test_logger_failure_preserves_stage_error():
    with patch.object(telemetry.logger, "info", side_effect=RuntimeError):
        with pytest.raises(ValueError, match="evidence"):
            with telemetry.stage("expansion", "search", 2000, 2000):
                raise ValueError("evidence")


def test_logging_latency_is_not_charged_to_shared_budget():
    clock = [1.0]

    def slow_log(_message):
        clock[0] += 5

    with (
        patch.object(telemetry, "perf_counter", side_effect=lambda: clock[0]),
        patch.object(telemetry.logger, "info", side_effect=slow_log) as log,
    ):
        with telemetry.stage("expansion", "search", 1500, 1500) as timing:
            clock[0] += 0.1251
    assert timing.elapsed_ms == 126
    payload = json.loads(log.call_args.args[0])
    assert payload["budget_remaining_ms"] == 1374
