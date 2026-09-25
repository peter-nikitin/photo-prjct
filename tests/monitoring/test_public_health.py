import importlib.util
import json
import ssl
import sys
import urllib.error
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


def test_metadata_iam_token_authorizes_metric_write(
    probe: ModuleType, config: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = probe.ProbeConfig(
        target=config.target,
        folder_id=config.folder_id,
        check_name=config.check_name,
        metadata_iam_token=True,
    )
    requests: list[tuple[str, str | None, str | None, float]] = []

    class FakeResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self) -> bytes:
            return self.body

    def fake_urlopen(request: object, timeout: float):
        requests.append(
            (
                request.full_url,
                request.get_header("Metadata-flavor"),
                request.get_header("Authorization"),
                timeout,
            )
        )
        if len(requests) == 1:
            return FakeResponse(
                b'{"access_token":"secret-token","expires_in":3600,"token_type":"Bearer"}'
            )
        return FakeResponse(b"")

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    probe.write_metrics(config, [{"name": "findme_probe_success", "value": 1.0}])

    assert requests == [
        (
            "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token",
            "Google",
            None,
            10.0,
        ),
        (
            "https://monitoring.api.cloud.yandex.net/monitoring/v2/data/write?folderId=folder-id&service=custom",
            None,
            "Bearer secret-token",
            10.0,
        ),
    ]


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b"{}",
        b'{"access_token":null}',
        b'{"access_token":""}',
        b'{"access_token":"secret-token\\nheader: injected"}',
    ],
)
def test_bad_metadata_response_prevents_write_without_leaking_token(
    probe: ModuleType, config: object, monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    config = probe.ProbeConfig(
        target=config.target,
        folder_id=config.folder_id,
        check_name=config.check_name,
        metadata_iam_token=True,
    )
    requests: list[str] = []
    output: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self) -> bytes:
            return body

    def fake_urlopen(request: object, timeout: float):
        requests.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    result = probe.run_probe(
        config,
        fetch_health=lambda target, timeout: _ok_response(probe),
        certificate_not_after=lambda target, timeout: _expires_in_two_days(),
        monotonic_clock=iter((1.0, 1.1)).__next__,
        wall_clock=lambda: 1_767_225_600.0,
        emit=output.append,
    )

    assert result == 1
    assert requests == [
        "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
    ]
    assert output == ["metrics write failed"]


def test_metadata_transport_error_prevents_write_and_keeps_diagnostics_safe(
    probe: ModuleType, config: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = probe.ProbeConfig(
        target=config.target,
        folder_id=config.folder_id,
        check_name=config.check_name,
        metadata_iam_token=True,
    )
    output: list[str] = []

    def fail_urlopen(request: object, timeout: float):
        raise urllib.error.URLError("secret-token")

    monkeypatch.setattr(probe.urllib.request, "urlopen", fail_urlopen)
    result = probe.run_probe(
        config,
        fetch_health=lambda target, timeout: _ok_response(probe),
        certificate_not_after=lambda target, timeout: _expires_in_two_days(),
        monotonic_clock=iter((1.0, 1.1)).__next__,
        wall_clock=lambda: 1_767_225_600.0,
        emit=output.append,
    )

    assert result == 1
    assert output == ["metrics write failed"]


def test_failed_public_check_writes_zero_using_metadata_token(
    probe: ModuleType, config: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = probe.ProbeConfig(
        target=config.target,
        folder_id=config.folder_id,
        check_name=config.check_name,
        metadata_iam_token=True,
    )
    writes: list[dict[str, object]] = []

    class FakeResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self) -> bytes:
            return self.body

    def fake_urlopen(request: object, timeout: float):
        if request.full_url == probe.METADATA_TOKEN_URL:
            return FakeResponse(
                b'{"access_token":"secret-token","expires_in":3600,"token_type":"Bearer"}'
            )
        writes.append(
            {
                "authorization": request.get_header("Authorization"),
                "payload": json.loads(request.data),
            }
        )
        return FakeResponse(b"")

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    result = probe.run_probe(
        config,
        fetch_health=lambda target, timeout: probe.HealthResponse(status=503, body=b""),
        certificate_not_after=lambda target, timeout: _expires_in_two_days(),
        monotonic_clock=iter((1.0, 1.1)).__next__,
        wall_clock=lambda: 1_767_225_600.0,
        emit=lambda message: None,
    )

    assert result == 1
    assert len(writes) == 1
    assert writes[0]["authorization"] == "Bearer secret-token"
    assert writes[0]["payload"]["metrics"][0]["value"] == 0.0


def test_cli_accepts_exactly_one_authentication_mode(probe: ModuleType) -> None:
    base = [
        "--target",
        "https://findme-photo.ru/health/",
        "--folder-id",
        "folder-id",
        "--check",
        "canonical-health",
    ]

    assert probe.parse_arguments([*base, "--auth", "vm-metadata"]).metadata_iam_token is True
    assert probe.parse_arguments([*base, "--api-key", "secret"]).api_key == "secret"
    with pytest.raises(SystemExit):
        probe.parse_arguments(base)
    with pytest.raises(SystemExit):
        probe.parse_arguments([*base, "--auth", "vm-metadata", "--api-key", "secret"])
