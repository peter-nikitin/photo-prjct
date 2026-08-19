import importlib.util
import json
import ssl
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "monitor_public_health.py"


def _load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("monitor_public_health", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def probe() -> ModuleType:
    return _load_probe()


@pytest.fixture
def config(probe: ModuleType):
    return probe.ProbeConfig(
        target="https://findme-photo.ru/health/",
        folder_id="folder-id",
        check_name="canonical-health",
        api_key="api-key-that-must-not-leak",
    )


def _ok_response(probe: ModuleType):
    return probe.HealthResponse(status=200, body=b'{"status":"ok"}')


def _expires_in_two_days() -> str:
    return "Jan 03 00:00:00 2026 GMT"


def test_success_writes_only_the_agreed_metrics_and_bounded_labels(
    probe: ModuleType, config: object
) -> None:
    written: list[list[dict[str, object]]] = []

    exit_code = probe.run_probe(
        config,
        fetch_health=lambda target, timeout: _ok_response(probe),
        certificate_not_after=lambda target, timeout: _expires_in_two_days(),
        monotonic_clock=iter((10.0, 10.25)).__next__,
        wall_clock=lambda: 1_767_225_600.0,
        metric_writer=lambda received_config, metrics: written.append(metrics),
        emit=lambda message: None,
    )

    assert exit_code == 0
    assert len(written) == 1
    metrics = written[0]
    assert {metric["name"] for metric in metrics} == {
        "findme_probe_success",
        "findme_probe_duration_seconds",
        "findme_probe_tls_days_remaining",
    }
    assert all(metric["labels"] == {"check": "canonical-health"} for metric in metrics)
    assert all(metric["type"] == "DGAUGE" for metric in metrics)
    assert (
        next(metric for metric in metrics if metric["name"] == "findme_probe_success")["value"]
        == 1.0
    )
    assert (
        next(metric for metric in metrics if metric["name"] == "findme_probe_duration_seconds")[
            "value"
        ]
        == 0.25
    )
    assert (
        next(metric for metric in metrics if metric["name"] == "findme_probe_tls_days_remaining")[
            "value"
        ]
        == 2.0
    )


@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        ("wrong-status", "unexpected HTTP status"),
        ("wrong-body", "unexpected health response"),
    ],
)
def test_completed_bad_response_still_writes_failed_observation(
    probe: ModuleType, config: object, response: str, expected_reason: str
) -> None:
    written: list[list[dict[str, object]]] = []
    output: list[str] = []
    health_response = (
        probe.HealthResponse(status=503, body=b'{"status":"ok"}')
        if response == "wrong-status"
        else probe.HealthResponse(status=200, body=b'{"status":"not-ok"}')
    )

    exit_code = probe.run_probe(
        config,
        fetch_health=lambda target, timeout: health_response,
        certificate_not_after=lambda target, timeout: _expires_in_two_days(),
        monotonic_clock=iter((1.0, 1.5)).__next__,
        wall_clock=lambda: 1_767_225_600.0,
        metric_writer=lambda received_config, metrics: written.append(metrics),
        emit=output.append,
    )

    assert exit_code == 1
    assert (
        next(metric for metric in written[0] if metric["name"] == "findme_probe_success")["value"]
        == 0.0
    )
    assert any(expected_reason in message for message in output)


@pytest.mark.parametrize(
    "failure",
    [
        ssl.SSLCertVerificationError("certificate verification failed"),
        TimeoutError("connection timed out"),
    ],
)
def test_transport_failure_writes_failed_observation_without_tls_metric(
    probe: ModuleType, config: object, failure: Exception
) -> None:
    written: list[list[dict[str, object]]] = []

    def fail_fetch(target: str, timeout: float):
        raise failure

    exit_code = probe.run_probe(
        config,
        fetch_health=fail_fetch,
        certificate_not_after=lambda target, timeout: pytest.fail("TLS must not be inspected"),
        monotonic_clock=iter((3.0, 3.75)).__next__,
        wall_clock=lambda: 1_767_225_600.0,
        metric_writer=lambda received_config, metrics: written.append(metrics),
        emit=lambda message: None,
    )

    assert exit_code == 1
    assert {metric["name"] for metric in written[0]} == {
        "findme_probe_success",
        "findme_probe_duration_seconds",
    }
    assert (
        next(metric for metric in written[0] if metric["name"] == "findme_probe_success")["value"]
        == 0.0
    )


def test_tls_validation_failure_after_response_marks_probe_failed_without_lifetime(
    probe: ModuleType, config: object
) -> None:
    written: list[list[dict[str, object]]] = []

    def fail_tls(target: str, timeout: float) -> str:
        raise ssl.SSLCertVerificationError("certificate verification failed")

    exit_code = probe.run_probe(
        config,
        fetch_health=lambda target, timeout: _ok_response(probe),
        certificate_not_after=fail_tls,
        monotonic_clock=iter((4.0, 4.25)).__next__,
        wall_clock=lambda: 1_767_225_600.0,
        metric_writer=lambda received_config, metrics: written.append(metrics),
        emit=lambda message: None,
    )

    assert exit_code == 1
    assert {metric["name"] for metric in written[0]} == {
        "findme_probe_success",
        "findme_probe_duration_seconds",
    }


def test_metric_write_failure_is_reported_without_secret_or_authorization_header(
    probe: ModuleType, config: object
) -> None:
    output: list[str] = []

    def fail_write(received_config: object, metrics: list[dict[str, object]]) -> None:
        raise RuntimeError("Authorization: Bearer api-key-that-must-not-leak")

    exit_code = probe.run_probe(
        config,
        fetch_health=lambda target, timeout: _ok_response(probe),
        certificate_not_after=lambda target, timeout: _expires_in_two_days(),
        monotonic_clock=iter((1.0, 1.1)).__next__,
        wall_clock=lambda: 1_767_225_600.0,
        metric_writer=fail_write,
        emit=output.append,
    )

    assert exit_code == 1
    rendered_output = "\n".join(output)
    assert "metrics write failed" in rendered_output
    assert "api-key-that-must-not-leak" not in rendered_output
    assert "Authorization" not in rendered_output


def test_probe_captures_only_agreed_metrics_for_local_deterministic_boundaries(
    probe: ModuleType, config: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self) -> bytes:
            return b""

    def fake_urlopen(request: object, timeout: float):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    exit_code = probe.run_probe(
        config,
        fetch_health=lambda target, timeout: _ok_response(probe),
        certificate_not_after=lambda target, timeout: _expires_in_two_days(),
        monotonic_clock=iter((5.0, 5.5)).__next__,
        wall_clock=lambda: 1_767_225_600.0,
        emit=lambda message: None,
    )

    assert exit_code == 0
    assert captured["url"] == (
        "https://monitoring.api.cloud.yandex.net/monitoring/v2/data/write?folderId=folder-id&service=custom"
    )
    assert captured["authorization"] == "Api-Key api-key-that-must-not-leak"
    assert captured["payload"] == {
        "metrics": [
            {
                "name": "findme_probe_success",
                "labels": {"check": "canonical-health"},
                "value": 1.0,
                "type": "DGAUGE",
            },
            {
                "name": "findme_probe_duration_seconds",
                "labels": {"check": "canonical-health"},
                "value": 0.5,
                "type": "DGAUGE",
            },
            {
                "name": "findme_probe_tls_days_remaining",
                "labels": {"check": "canonical-health"},
                "value": 2.0,
                "type": "DGAUGE",
            },
        ]
    }
