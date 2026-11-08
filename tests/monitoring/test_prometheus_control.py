import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "deploy/monitoring/prometheus/control.py"


@pytest.fixture
def control():
    spec = importlib.util.spec_from_file_location("test_control", PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_monitoring_get_retries_bounded_timeout_without_repeating_put(control, monkeypatch):
    transport = control.CloudTransport.__new__(control.CloudTransport)
    transport.config = control.load_config()
    transport.token = "secret-never-print"
    attempts = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"files": []}'

    def timed_out_once(request, timeout):
        attempts.append(request.get_method())
        if len(attempts) == 1:
            raise TimeoutError("secret-never-print")
        return Response()

    monkeypatch.setattr(control, "urlopen", timed_out_once)
    monkeypatch.setattr(control.time, "sleep", lambda _seconds: None)
    assert transport.request("GET", "/extensions/v1/rules") == {"files": []}
    assert attempts == ["GET", "GET"]

    attempts.clear()
    with pytest.raises(control.ControlError) as error:
        transport.request("PUT", "/extensions/v1/rules", {"content": "anything"})
    assert attempts == ["PUT"]
    assert "secret-never-print" not in str(error.value)

    attempts.clear()

    def always_timeout(request, timeout):
        attempts.append(request.get_method())
        raise TimeoutError("secret-never-print")

    monkeypatch.setattr(control, "urlopen", always_timeout)
    with pytest.raises(control.ControlError) as error:
        transport.request("GET", "/extensions/v1/rules")
    assert attempts == ["GET", "GET", "GET"]
    assert "secret-never-print" not in str(error.value)


@pytest.mark.parametrize(
    "response",
    [
        {"status": "error", "data": {"resultType": "matrix", "result": []}},
        {"status": "success", "data": {"resultType": "vector", "result": []}},
        {"status": "success", "data": {"resultType": "matrix", "result": []}},
        {"status": "success", "data": {"resultType": "matrix", "result": [{"values": []}]}},
        {"status": "success", "data": {"resultType": "matrix", "result": [{"values": [[990]]}]}},
        {"status": "success", "data": {"resultType": "matrix", "result": [{"values": [[0, "1"]]}]}},
        {
            "status": "success",
            "data": {"resultType": "matrix", "result": [{"values": [[1001, "1"]]}]},
        },
        {
            "status": "success",
            "data": {"resultType": "matrix", "result": [{"values": [[990, "NaN"]]}]},
        },
        {
            "status": "success",
            "data": {"resultType": "matrix", "result": [{"values": [["NaN", "1"]]}]},
        },
    ],
)
def test_matrix_freshness_rejects_failed_missing_malformed_or_invalid_points(control, response):
    class Transport:
        def request(self, *_args):
            return response

    with pytest.raises(control.ControlError, match="sample"):
        control._fresh_matrix(Transport(), "up[180s]", key="up", max_age=180, now=1000)


def test_matrix_freshness_rejects_stale_latest_point_in_any_observed_series(control):
    class Transport:
        def request(self, *_args):
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {"values": [[990, "1"]]},
                        {"values": [[0, "1"]]},
                    ],
                },
            }

    with pytest.raises(control.ControlError, match="sample"):
        control._fresh_matrix(Transport(), "up[180s]", key="up", max_age=180, now=1000)
