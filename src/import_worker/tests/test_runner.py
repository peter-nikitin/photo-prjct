from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from import_worker.contracts import Config
from import_worker.runner import Lease, LeaseLost, Runner
from import_worker.transport import TransportError


def test_renewal_failure_stops_side_effects_and_does_not_extend_lease():
    client = Mock()
    lease = Lease(client, str(uuid4()), clock=lambda: 10)
    client.call.side_effect = TransportError()
    with pytest.raises(LeaseLost):
        lease.check()
    assert lease.deadline == 0


def test_expired_local_lease_never_attempts_renewal():
    client = Mock()
    clock = Mock(side_effect=[1, 1, 112])
    lease = Lease(client, str(uuid4()), clock=clock)
    lease.check()
    with pytest.raises(LeaseLost):
        lease.check()
    assert client.call.call_count == 1


def test_restart_cleans_one_temp_file_and_lock_prevents_concurrent_deletion(tmp_path: Path):
    current = tmp_path / "current.jpg"
    current.write_bytes(b"interrupted")
    config = Config("http://web:8000/internal/photo-import/v1/", "token", tmp_path)
    runner = Runner(config, Mock(), Mock(), Mock())
    assert not current.exists()
    current.write_bytes(b"active")
    with pytest.raises(RuntimeError):
        Runner(config, Mock(), Mock(), Mock())
    assert current.read_bytes() == b"active"
    runner.close()
    assert not current.exists()


def test_retry_after_is_observed_once_without_retrying_source_inside_claim(tmp_path):
    client, source, transport, sleep = Mock(), Mock(), Mock(), Mock()
    attempt = str(uuid4())
    client.call.return_value = {
        "work": dict(kind="file", attempt_id=attempt, source={"key": "key", "path": "/one.jpg"})
    }
    source.download_url.side_effect = TransportError(retryable=True, retry_after=120)
    runner = Runner(
        Config("http://web:8000/internal/photo-import/v1/", "token", tmp_path),
        client,
        source,
        transport,
        sleep=sleep,
    )
    try:
        assert runner.run_once()
    finally:
        runner.close()
    source.download_url.assert_called_once()
    sleep.assert_called_once_with(120)
    assert any(
        call.kwargs.get("operation") == "download" and call.kwargs.get("retryable")
        for call in client.callback.call_args_list
    )
    transport.upload.assert_not_called()


def test_exhausted_callback_transport_does_not_report_a_source_failure(tmp_path):
    from import_worker.client import APIClient

    config = Config("http://web/internal/photo-import/v1/", "token", tmp_path)
    attempt = str(uuid4())
    transport, source = Mock(), Mock()
    endpoints = []

    def response(method, url, **kwargs):
        endpoints.append(url)
        if url.endswith("/claim"):
            return {
                "contract_version": 1,
                "work": dict(
                    kind="manifest",
                    attempt_id=attempt,
                    batch_id=str(uuid4()),
                    source={"key": "token"},
                ),
            }
        if url.endswith("/renew"):
            return {"contract_version": 1}
        raise TransportError(retryable=True)

    transport.json.side_effect = response
    source.pages.return_value = iter([("canonical", [])])
    runner = Runner(
        config, APIClient(config, transport), source, transport, sleep=lambda seconds: None
    )
    try:
        assert runner.run_once()
    finally:
        runner.close()
    assert sum(url.endswith("/manifest/pages") for url in endpoints) == 4
    assert not any(url.endswith("/fail") for url in endpoints)
    source.pages.assert_called_once()


def test_manifest_callback_splits_worst_case_unicode_on_entire_wire_envelope(tmp_path):
    """100 maximum valid entries exceed 1MiB after escaping and must still persist."""
    import json
    from unittest.mock import Mock

    from import_worker.client import APIClient
    from import_worker.contracts import Config
    from import_worker.runner import Runner

    config = Config("http://web/internal/photo-import/v1/", "token", tmp_path)
    transport = Mock()
    transport.json.return_value = {"contract_version": 1}
    entries = [
        dict(
            path="/" + "😀" * 1018 + f"{i:05}",
            name="😀" * 251 + ".jpg",
            kind="jpeg",
            size=52428800,
            version="😀" * 255,
            sha256="a" * 64,
            md5="b" * 32,
        )
        for i in range(100)
    ]
    source = Mock()
    source.pages.return_value = [("canonical", entries)]
    lease = Mock()
    lease.attempt = "00000000-0000-4000-8000-000000000001"
    runner = Runner(config, APIClient(config, transport), source, transport)
    try:
        runner._manifest(
            dict(batch_id="00000000-0000-4000-8000-000000000002", source={"key": "key"}), lease
        )
    finally:
        runner.close()
    calls = [
        call for call in transport.json.call_args_list if call.args[1].endswith("manifest/pages")
    ]
    assert len(calls) > 1
    assert all(len(call.kwargs["body"]) <= Config.json_bytes for call in calls)
    pages = [json.loads(call.kwargs["body"]) for call in calls]
    assert [page["page_number"] for page in pages] == list(range(len(pages)))
    assert [entry for page in pages for entry in page["entries"]] == entries
