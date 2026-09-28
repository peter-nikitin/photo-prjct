from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from photo_worker.client import HttpClient
from photo_worker.lifecycle import DrainController, FleetLifecycle, HostIdentity
from photo_worker.telemetry import RuntimeTelemetry, start_runtime_server
from photo_worker.transport import REMOTE_API_URL
from prometheus_client.parser import text_string_to_metric_families


def samples(telemetry: RuntimeTelemetry) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    _, body = telemetry.scrape()
    return {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(body.decode())
        for sample in family.samples
    }


def test_runtime_aggregates_are_bounded_and_busy_resets() -> None:
    telemetry = RuntimeTelemetry(lambda: "00000000-0000-0000-0000-000000000001")
    telemetry.started("capture_metadata")
    assert samples(telemetry)[("worker_runtime_busy", ())] == 1
    telemetry.finished("capture_metadata", "callback_delivered", 16)
    values = samples(telemetry)
    assert values[("worker_runtime_busy", ())] == 0
    labels = (("kind", "capture_metadata"), ("outcome", "callback_delivered"))
    assert values[("worker_runtime_executions_total", labels)] == 1
    assert values[("worker_runtime_execution_duration_seconds_count", labels)] == 1
    assert values[("worker_runtime_execution_duration_seconds_sum", labels)] == 16
    bucket_values = {
        dict(labels)["le"]: value
        for (name, labels), value in values.items()
        if name == "worker_runtime_execution_duration_seconds_bucket"
    }
    assert bucket_values == {
        "1.0": 0,
        "5.0": 0,
        "15.0": 0,
        "60.0": 1,
        "300.0": 1,
        "900.0": 1,
        "1800.0": 1,
        "+Inf": 1,
    }
    assert all(name.startswith("worker_runtime_") for name, _ in values)
    assert all(not name.endswith("_created") for name, _ in values)
    with pytest.raises(ValueError):
        telemetry.started("photo-person-secret")
    with pytest.raises(ValueError):
        telemetry.started("face_embedding_benchmark")
    with pytest.raises(ValueError):
        telemetry.finished("capture_metadata", "exception-secret", 1)


def test_real_loopback_scrape_fences_current_registration_and_resets_baseline() -> None:
    client = HttpClient(REMOTE_API_URL, "fleet", transport="remote")
    fleet = FleetLifecycle(
        client,
        HostIdentity("selfie", "instance-1", "00000000-0000-0000-0000-000000000003", "a" * 40),
        DrainController(),
    )
    client.bind_member(fleet._identity.envelope())
    generations = iter(
        [
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        ]
    )
    client.post_json = lambda *_args, **_kwargs: {"registration_generation": next(generations)}
    telemetry = RuntimeTelemetry(lambda: fleet.registration_generation)
    server = start_runtime_server(telemetry, port=0)
    assert server.server_address[0] == "127.0.0.1"
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(HTTPError) as missing:
            urlopen(url + "/metrics", timeout=2)
        assert missing.value.code == 503
        client.member_request("register")
        telemetry.started("selfie_query")
        telemetry.finished("selfie_query", "callback_delivered", 2)
        with urlopen(url + "/metrics", timeout=2) as response:
            assert response.headers["X-Worker-Registration-Generation"] == (
                "00000000-0000-0000-0000-000000000001"
            )
            body = response.read().decode()
        assert (
            'worker_runtime_executions_total{kind="selfie_query",outcome="callback_delivered"} 1.0'
            in body
        )
        assert "registration_generation" not in body
        client.member_request("register")
        with urlopen(url + "/status", timeout=2) as response:
            assert json.load(response) == {
                "registration_generation": "00000000-0000-0000-0000-000000000002"
            }
        with urlopen(url + "/metrics", timeout=2) as response:
            assert response.headers["X-Worker-Registration-Generation"].endswith("2")
            assert "worker_runtime_executions_total{" not in response.read().decode()
        with pytest.raises(HTTPError) as unknown:
            urlopen(url + "/anything", timeout=2)
        assert unknown.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(("pool", "enabled"), [(None, False), ("selfie", False), ("selfie", True)])
def test_entrypoint_exposes_runtime_only_with_explicit_remote_opt_in(
    pool, enabled, monkeypatch
) -> None:
    from photo_worker import __main__ as entrypoint
    from photo_worker.runner import Worker, WorkerConfig

    config = WorkerConfig("a" * 40, 120, remote_pool=pool, runtime_telemetry_enabled=enabled)
    client = HttpClient(REMOTE_API_URL, "fleet", transport="remote")
    client.post_json = lambda *_args, **_kwargs: {
        "registration_generation": "00000000-0000-0000-0000-000000000001",
        "ready": True,
    }
    monkeypatch.setattr(WorkerConfig, "from_env", lambda: (config, client))
    monkeypatch.setattr(entrypoint, "install_signal_handlers", lambda _worker: None)
    monkeypatch.setattr(
        HostIdentity,
        "read",
        lambda **_kwargs: HostIdentity(
            "selfie", "instance-1", "00000000-0000-0000-0000-000000000003", "a" * 40
        ),
    )
    monkeypatch.setattr("photo_worker.lifecycle._warm_models", lambda: None)
    servers = []

    def start(telemetry, **_kwargs):
        server = start_runtime_server(telemetry, port=0)
        servers.append(server)
        return server

    monkeypatch.setattr(entrypoint, "start_runtime_server", start)

    def run(worker):
        assert (worker.telemetry is not None) == enabled
        if enabled:
            with urlopen(
                f"http://127.0.0.1:{servers[0].server_port}/metrics", timeout=2
            ) as response:
                assert b"worker_runtime_busy 0.0" in response.read()
        else:
            assert servers == []

    monkeypatch.setattr(Worker, "run_forever", run)
    entrypoint.main()
    if enabled:
        assert servers[0].fileno() == -1


def test_local_worker_cannot_enable_runtime_endpoint() -> None:
    from photo_worker.runner import WorkerConfig

    with pytest.raises(ValueError, match="remote"):
        WorkerConfig("a" * 40, 120, runtime_telemetry_enabled=True)


def test_registration_changed_during_execution_discards_old_work_but_keeps_busy() -> None:
    client = HttpClient(REMOTE_API_URL, "fleet", transport="remote")
    identity = HostIdentity(
        "selfie", "instance-1", "00000000-0000-0000-0000-000000000003", "a" * 40
    )
    client.bind_member(identity.envelope())
    generations = iter(
        ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"]
    )
    client.post_json = lambda *_args, **_kwargs: {"registration_generation": next(generations)}
    fleet = FleetLifecycle(client, identity, DrainController())
    telemetry = RuntimeTelemetry(lambda: fleet.registration_generation)
    client.member_request("register")
    telemetry.started("selfie_query")
    client.member_request("register")
    assert samples(telemetry)[("worker_runtime_busy", ())] == 1
    telemetry.finished("selfie_query", "callback_delivered", 2)
    values = samples(telemetry)
    assert values[("worker_runtime_busy", ())] == 0
    assert all(name != "worker_runtime_executions_total" for name, _ in values)
    telemetry.started("selfie_query")
    telemetry.finished("selfie_query", "callback_delivered", 3)
    assert (
        samples(telemetry)[
            (
                "worker_runtime_executions_total",
                (("kind", "selfie_query"), ("outcome", "callback_delivered")),
            )
        ]
        == 1
    )


def test_endpoint_bind_failure_keeps_remote_worker_running(monkeypatch) -> None:
    from photo_worker import __main__ as entrypoint
    from photo_worker.runner import Worker, WorkerConfig

    config = WorkerConfig("a" * 40, 120, remote_pool="selfie", runtime_telemetry_enabled=True)
    client = HttpClient(REMOTE_API_URL, "fleet", transport="remote")
    client.post_json = lambda *_args, **_kwargs: {
        "registration_generation": "00000000-0000-0000-0000-000000000001",
        "ready": True,
    }
    monkeypatch.setattr(WorkerConfig, "from_env", lambda: (config, client))
    monkeypatch.setattr(entrypoint, "install_signal_handlers", lambda _worker: None)
    monkeypatch.setattr(
        HostIdentity,
        "read",
        lambda **_kwargs: HostIdentity(
            "selfie", "instance-1", "00000000-0000-0000-0000-000000000003", "a" * 40
        ),
    )
    monkeypatch.setattr("photo_worker.lifecycle._warm_models", lambda: None)
    monkeypatch.setattr(
        entrypoint,
        "start_runtime_server",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("bind failed")),
    )
    observed = []
    monkeypatch.setattr(
        Worker, "run_forever", lambda worker: observed.append(worker.fleet.can_claim)
    )
    entrypoint.main()
    assert observed == [True]
