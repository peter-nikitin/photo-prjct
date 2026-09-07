import json
from unittest.mock import Mock

import pytest
from import_worker.client import APIClient
from import_worker.contracts import Config
from import_worker.transport import TransportError


def test_token_is_exclusive_to_configured_private_api_and_v1_is_serialized():
    transport = Mock()
    transport.json.return_value = {"contract_version": 1, "work": None}
    client = APIClient(
        Config("http://web:8000/internal/photo-import/v1/", "private-token"), transport
    )
    assert client.call("claim", lease_seconds=120)["work"] is None
    args, kwargs = transport.json.call_args
    assert args == ("POST", "http://web:8000/internal/photo-import/v1/claim")
    assert kwargs["headers"]["Authorization"] == "Bearer private-token"
    assert json.loads(kwargs["body"]) == {"contract_version": 1, "lease_seconds": 120}
    with pytest.raises(ValueError):
        client.call("https://evil.test/claim")
    assert transport.json.call_count == 1


def test_unknown_contract_version_is_not_used():
    transport = Mock()
    transport.json.return_value = {"contract_version": True}
    with pytest.raises(TransportError):
        APIClient(Config("http://web:8000/internal/photo-import/v1/", "token"), transport).call(
            "claim"
        )


def test_callback_retries_are_bounded_and_claim_is_never_replayed():
    from uuid import uuid4

    from import_worker.client import CallbackUnavailable

    transport = Mock()
    transport.json.side_effect = TransportError(retryable=True)
    client = APIClient(Config("http://web/internal/photo-import/v1/", "token"), transport)
    with pytest.raises(TransportError):
        client.call("claim")
    assert transport.json.call_count == 1
    transport.reset_mock()
    with pytest.raises(CallbackUnavailable):
        client.callback(
            f"attempts/{uuid4()}/manifest/pages", check=lambda: None, sleep=lambda seconds: None
        )
    assert transport.json.call_count == 4


def test_callback_retry_after_checks_lease_between_bounded_waits():
    from uuid import uuid4

    from import_worker.runner import LeaseLost

    transport, check, sleep = Mock(), Mock(), Mock()
    transport.json.side_effect = TransportError(retryable=True, retry_after=120)
    check.side_effect = [None, None, LeaseLost()]
    client = APIClient(Config("http://web/internal/photo-import/v1/", "token"), transport)
    with pytest.raises(LeaseLost):
        client.callback(f"attempts/{uuid4()}/prepare-upload", check=check, sleep=sleep)
    assert transport.json.call_count == 1
    assert all(call.args[0] <= Config.heartbeat_seconds for call in sleep.call_args_list)


def test_startup_readiness_fails_closed_without_constructing_runner(monkeypatch, tmp_path):
    from unittest.mock import Mock

    import pytest
    from import_worker.transport import TransportError

    from import_worker import __main__ as entrypoint

    monkeypatch.setenv("PHOTO_IMPORT_API_URL", "http://web/internal/photo-import/v1/")
    monkeypatch.setenv("PHOTO_IMPORT_WORKER_TOKEN", "token")
    monkeypatch.setenv("PHOTO_IMPORT_TEMP_DIR", str(tmp_path))
    runner = Mock()
    monkeypatch.setattr(entrypoint, "Runner", runner)
    for status in (401, 404):
        client = Mock()
        client.call.side_effect = TransportError(status=status)
        monkeypatch.setattr(entrypoint, "APIClient", Mock(return_value=client))
        with pytest.raises(TransportError):
            entrypoint.main()
        client.call.assert_called_once_with("readiness")
        runner.assert_not_called()
