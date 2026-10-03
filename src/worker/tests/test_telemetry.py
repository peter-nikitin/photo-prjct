from __future__ import annotations

import json
import threading
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


def test_runtime_counts_two_independent_inflight_executions() -> None:
    telemetry = RuntimeTelemetry(lambda: "00000000-0000-0000-0000-000000000001")
    ready = threading.Barrier(3)
    release = threading.Event()

    def execute() -> None:
        telemetry.started("face_embedding")
        ready.wait(timeout=3)
        release.wait(timeout=3)
        telemetry.finished("face_embedding", "callback_delivered", 2)

    threads = [threading.Thread(target=execute) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        ready.wait(timeout=3)
        assert samples(telemetry)[("worker_runtime_busy", ())] == 2
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=3)
    values = samples(telemetry)
    assert values[("worker_runtime_busy", ())] == 0
    labels = (("kind", "face_embedding"), ("outcome", "callback_delivered"))
    assert values[("worker_runtime_executions_total", labels)] == 2


def test_bulk_entrypoint_runs_two_slots_and_drains_together(monkeypatch) -> None:
    from photo_worker import __main__ as entrypoint
    from photo_worker.runner import Worker, WorkerConfig

    config = WorkerConfig("a" * 40, 120, concurrency=2, remote_pool="bulk")
    client = HttpClient(REMOTE_API_URL, "fleet", transport="remote")
    monkeypatch.setattr(WorkerConfig, "from_env", lambda: (config, client))
    monkeypatch.setattr(entrypoint, "install_signal_handlers", lambda _worker: None)
    monkeypatch.setattr(
        HostIdentity,
        "read",
        lambda **_kwargs: HostIdentity(
            "bulk", "instance-1", "00000000-0000-0000-0000-000000000003", "a" * 40
        ),
    )
    monkeypatch.setattr("photo_worker.lifecycle._warm_models", lambda: None)
    warmed_threads = set()

    def warm_models() -> None:
        warmed_threads.add(threading.get_ident())

    monkeypatch.setattr(entrypoint, "warm_models", warm_models, raising=False)

    def start(fleet: FleetLifecycle) -> None:
        fleet._warmup()
        fleet._warm = True
        fleet._admitted = True

    monkeypatch.setattr(FleetLifecycle, "start", start)
    monkeypatch.setattr(FleetLifecycle, "pulse", lambda _fleet: None)
    monkeypatch.setattr(FleetLifecycle, "close", lambda _fleet: None)
    entered = threading.Barrier(3)
    release = threading.Event()
    workers = []

    def run(worker: Worker) -> None:
        assert threading.get_ident() in warmed_threads
        workers.append(worker)
        entered.wait(timeout=3)
        release.wait(timeout=3)

    monkeypatch.setattr(Worker, "run_forever", run)
    main_thread = threading.Thread(target=entrypoint.main)
    main_thread.start()
    try:
        entered.wait(timeout=3)
        assert len(workers) == 2
        assert workers[0] is not workers[1]
        assert workers[0].fleet is workers[1].fleet
        assert workers[0].drain is workers[1].drain
        assert not workers[0].drain.completed.is_set()
    finally:
        if workers:
            workers[0].request_drain()
        release.set()
        main_thread.join(timeout=3)
    assert not main_thread.is_alive()
    assert workers[0].drain.completed.is_set()


def test_bulk_slot_failure_keeps_drain_deadline_until_other_slot_finishes(monkeypatch) -> None:
    from photo_worker import __main__ as entrypoint
    from photo_worker.runner import Worker, WorkerConfig

    config = WorkerConfig("a" * 40, 120, concurrency=2, remote_pool="bulk")
    client = HttpClient(REMOTE_API_URL, "fleet", transport="remote")
    monkeypatch.setattr(WorkerConfig, "from_env", lambda: (config, client))
    monkeypatch.setattr(entrypoint, "install_signal_handlers", lambda _worker: None)
    monkeypatch.setattr(entrypoint, "warm_models", lambda: None)
    monkeypatch.setattr(
        HostIdentity,
        "read",
        lambda **_kwargs: HostIdentity(
            "bulk", "instance-1", "00000000-0000-0000-0000-000000000003", "a" * 40
        ),
    )

    def start(fleet: FleetLifecycle) -> None:
        fleet._warmup()

    monkeypatch.setattr(FleetLifecycle, "start", start)
    monkeypatch.setattr(FleetLifecycle, "pulse", lambda _fleet: None)
    monkeypatch.setattr(FleetLifecycle, "close", lambda _fleet: None)
    entered = threading.Barrier(3)
    release = threading.Event()
    workers = []
    errors = []

    def run(worker: Worker) -> None:
        workers.append(worker)
        entered.wait(timeout=3)
        if worker is workers[0]:
            raise RuntimeError("slot failed")
        release.wait(timeout=3)

    def run_main() -> None:
        try:
            entrypoint.main()
        except RuntimeError as error:
            errors.append(str(error))

    monkeypatch.setattr(Worker, "run_forever", run)
    main_thread = threading.Thread(target=run_main)
    main_thread.start()
    try:
        entered.wait(timeout=3)
        assert workers[0].draining.wait(timeout=3)
        assert not workers[0].drain.completed.is_set()
        assert main_thread.is_alive()
    finally:
        release.set()
        main_thread.join(timeout=3)
    assert errors == ["slot failed"]
    assert workers[0].drain.completed.is_set()


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
    telemetry = RuntimeTelemetry(
        lambda: fleet.registration_generation, ready=lambda: fleet.can_claim
    )
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
                "registration_generation": "00000000-0000-0000-0000-000000000002",
                "ready": False,
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


def test_runtime_status_reports_admitted_ready_and_draining_process() -> None:
    client = HttpClient(REMOTE_API_URL, "fleet", transport="remote")
    client.post_json = lambda *_args, **_kwargs: {
        "registration_generation": "00000000-0000-0000-0000-000000000001",
        "ready": True,
    }
    drain = DrainController()
    fleet = FleetLifecycle(
        client,
        HostIdentity("selfie", "instance-1", "00000000-0000-0000-0000-000000000003", "a" * 40),
        drain,
        warmup=lambda: None,
    )
    telemetry = RuntimeTelemetry(
        lambda: fleet.registration_generation, ready=lambda: fleet.can_claim
    )
    server = start_runtime_server(telemetry, port=0)
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(HTTPError) as cold:
            urlopen(url + "/status", timeout=2)
        assert cold.value.code == 503
        fleet.start()
        with urlopen(url + "/status", timeout=2) as response:
            assert json.load(response)["ready"] is True
        drain.request()
        with urlopen(url + "/status", timeout=2) as response:
            assert json.load(response)["ready"] is False
        with urlopen(url + "/metrics", timeout=2) as response:
            assert response.status == 200
    finally:
        drain.completed.set()
        fleet.close()
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
