import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from photo_worker.client import ApiError, HttpClient
from photo_worker.contracts import Claim
from photo_worker.lifecycle import (
    DrainController,
    FleetLifecycle,
    HostIdentity,
    install_signal_handlers,
)
from photo_worker.runner import Worker, WorkerConfig
from photo_worker.transport import REMOTE_API_URL


def identity():
    return HostIdentity("selfie", "instance-1", str(uuid4()), "a" * 40)


def test_host_identity_reads_stable_readonly_boot_and_rejects_invalid(tmp_path):
    boot = tmp_path / "boot"
    instance = tmp_path / "instance"
    boot.write_text(str(uuid4()) + "\n")
    instance.write_text("instance-1\n")
    first = HostIdentity.read(pool="selfie", build="a" * 40, boot_path=boot, instance_path=instance)
    assert first == HostIdentity.read(
        pool="selfie", build="a" * 40, boot_path=boot, instance_path=instance
    )
    boot.write_text("not-a-boot")
    with pytest.raises(ValueError):
        HostIdentity.read(pool="selfie", build="a" * 40, boot_path=boot, instance_path=instance)


def test_worker_signals_before_claim_and_during_processing_preserve_current_work(monkeypatch):
    client = Mock()
    client.claim_job.return_value = Claim(None, 2)
    done = threading.Event()
    worker = Worker(
        client,
        WorkerConfig("test", 120),
        drain=DrainController(completed=done, timeout_seconds=1, force_exit=Mock()),
    )
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda name, handler: handlers.setdefault(name, handler))
    install_signal_handlers(worker)
    handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert worker.run_once() is None
    client.claim_job.assert_not_called()
    done.set()


def test_lifecycle_cold_warm_heartbeat_and_irreversible_drain():
    client = Mock()
    client.member_request.return_value = {"ready": True, "draining": False}
    drain = DrainController(force_exit=Mock())
    warm = []
    lifecycle = FleetLifecycle(client, identity(), drain, warmup=lambda: warm.append(True))
    assert not lifecycle.can_claim
    lifecycle.start()
    try:
        assert warm == [True]
        assert lifecycle.can_claim
        drain.request()
        lifecycle.pulse()
        assert not lifecycle.can_claim
        assert client.member_request.call_args.args[0] == "heartbeat"
        assert client.member_request.call_args.kwargs == {"ready": False, "draining": True}
    finally:
        drain.completed.set()
        lifecycle.close()


def test_warmup_failure_never_reports_ready():
    client = Mock()
    lifecycle = FleetLifecycle(
        client, identity(), DrainController(), warmup=Mock(side_effect=ValueError("bad model"))
    )
    with pytest.raises(ValueError):
        lifecycle.start()
    lifecycle.close()
    assert not lifecycle.can_claim
    client.member_request.assert_not_called()
    assert all(not call.kwargs.get("ready") for call in client.member_request.call_args_list)


def test_replacement_warms_before_registration_transfers_claim_ownership():
    client = Mock()
    client.member_request.return_value = {"ready": True, "draining": False}
    phases = []

    def warm():
        client.member_request.assert_not_called()
        phases.append("warm")

    def register(operation, **_fields):
        assert operation == "register"
        assert phases == ["warm"]
        phases.append("register")
        return {"ready": True, "draining": False}

    client.member_request.side_effect = register
    lifecycle = FleetLifecycle(client, identity(), DrainController(), warmup=warm)
    try:
        lifecycle.start()
        assert lifecycle.can_claim
        assert phases == ["warm", "register"]
    finally:
        lifecycle.close()


def test_hung_warmup_has_bounded_startup_and_never_claims():
    expired = threading.Event()
    drain = DrainController()
    client = Mock()
    lifecycle = FleetLifecycle(
        client,
        identity(),
        drain,
        warmup=lambda: expired.wait(1),
        startup_timeout_seconds=0.01,
        force_exit=lambda _code: expired.set(),
    )
    lifecycle.start()
    lifecycle.close()
    assert expired.is_set()
    assert not drain.requested.is_set()
    assert not lifecycle.can_claim


def test_signal_drain_deadline_is_bounded_and_repeated_signals_do_not_extend_it(monkeypatch):
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda name, handler: handlers.setdefault(name, handler))
    forced = threading.Event()
    exits = []

    def force(code):
        exits.append(code)
        forced.set()

    worker = Worker(
        Mock(),
        WorkerConfig("test", 120),
        drain=DrainController(timeout_seconds=0.01, force_exit=force),
    )
    install_signal_handlers(worker)
    handlers[signal.SIGINT](signal.SIGINT, None)
    handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert forced.wait(1)
    assert exits == [1]


def test_remote_claim_contains_identity_and_local_claim_remains_unchanged():
    client = HttpClient(REMOTE_API_URL, "fleet", transport="remote")
    member = identity()
    client.bind_member(member.envelope())
    generation = str(uuid4())
    client.post_json = Mock(return_value={"registration_generation": generation})
    client.member_request("register")
    client.post_json.return_value = {"empty": True, "suggested_delay_seconds": 2}
    client.claim_job(
        lease_seconds=120,
        processor_type="selfie_query",
        processor_version=2,
    )
    payload = client.post_json.call_args.args[1]
    assert payload.items() >= member.envelope().items()
    assert payload["registration_generation"] == generation
    unbound = HttpClient(REMOTE_API_URL, "fleet", transport="remote")
    with pytest.raises(ValueError):
        unbound.claim_job(lease_seconds=120)


def test_member_retirement_uses_current_process_generation():
    client = HttpClient(REMOTE_API_URL, "fleet", transport="remote")
    member = identity()
    client.bind_member(member.envelope())
    with pytest.raises(ValueError):
        client.member_request("retire")
    generation = str(uuid4())
    client.post_json = Mock(return_value={"registration_generation": generation, "ready": True})
    client.member_request("register")
    client.post_json.return_value = {"grant": None}
    assert client.member_request("retire") == {"grant": None}
    assert client.post_json.call_args.args == (
        "members/retire",
        member.envelope() | {"registration_generation": generation},
    )


@pytest.mark.parametrize("signals", [(), (signal.SIGTERM, signal.SIGINT, signal.SIGTERM)])
def test_server_drain_starts_one_deadline_before_repeated_signals(monkeypatch, signals):
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda name, handler: handlers.setdefault(name, handler))
    client = Mock()
    client.member_request.return_value = {"ready": False, "draining": True}
    forced = threading.Event()
    exits = []

    def force(code):
        exits.append(code)
        forced.set()

    worker = Worker(
        client,
        WorkerConfig("test", 120),
        drain=DrainController(timeout_seconds=0.01, force_exit=force),
    )
    install_signal_handlers(worker)
    lifecycle = FleetLifecycle(client, identity(), worker.drain, warmup=lambda: None)
    lifecycle.start()
    try:
        assert worker.draining.is_set()
        for signum in signals:
            handlers[signum](signum, None)
        assert forced.wait(1), "server-originated drain must itself bound a hung attempt"
        lifecycle.pulse()
        assert not lifecycle.can_claim
        assert exits == [1]
    finally:
        lifecycle.close()


def test_retryable_startup_outage_remains_bounded_without_intentional_drain():
    client = Mock()
    expired = threading.Event()
    drain = DrainController()

    def request(name, **fields):
        if name == "register":
            raise ApiError("unavailable", retryable=True)
        return {"registration_generation": str(uuid4())}

    client.member_request.side_effect = request
    lifecycle = FleetLifecycle(
        client,
        identity(),
        drain,
        warmup=lambda: None,
        startup_timeout_seconds=0.01,
        force_exit=lambda _code: expired.set(),
    )
    lifecycle.start()
    lifecycle.close()
    assert expired.is_set()
    assert not drain.requested.is_set()
    assert not lifecycle.can_claim


def test_superseded_generation_drains_without_displacing_warmed_replacement():
    client = Mock()
    client.member_request.side_effect = [
        {"registration_generation": str(uuid4()), "ready": True, "draining": False},
        ApiError("registration_changed", retryable=True),
    ]
    drain = DrainController()
    lifecycle = FleetLifecycle(client, identity(), drain, warmup=lambda: None)
    lifecycle.start()
    try:
        lifecycle.pulse()
        assert not lifecycle.can_claim
        assert drain.requested.is_set()
        assert [call.args[0] for call in client.member_request.call_args_list] == [
            "register",
            "heartbeat",
        ]
    finally:
        drain.completed.set()
        lifecycle.close()


def test_registration_responses_are_installed_in_serial_order():
    client = HttpClient(REMOTE_API_URL, "fleet", transport="remote")
    member = identity()
    client.bind_member(member.envelope())
    entered, release, second_started, second_posted = (threading.Event() for _ in range(4))
    generations = [str(uuid4()), str(uuid4())]
    calls = []

    def post(path, payload):
        if path == "members/register":
            index = len(calls)
            calls.append(path)
            if index == 0:
                entered.set()
                assert release.wait(5)
            else:
                second_posted.set()
            return {"registration_generation": generations[index]}
        assert payload["registration_generation"] == generations[1]
        return {"empty": True, "suggested_delay_seconds": 2}

    client.post_json = Mock(side_effect=post)

    def second():
        second_started.set()
        return client.member_request("register")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(client.member_request, "register")
        assert entered.wait(5)
        later = executor.submit(second)
        assert second_started.wait(5)
        assert not second_posted.wait(0.03)
        release.set()
        first.result(timeout=5)
        later.result(timeout=5)
    client.claim_job(
        lease_seconds=120,
        processor_type="selfie_query",
        processor_version=2,
    )


def test_warm_models_exercises_cached_detector_and_both_recognizers(monkeypatch):
    from photo_worker import face_embedding
    from photo_worker.adaface import ADAFACE_MODEL_NAME

    models = []
    runtimes = {}

    def runtime(_cv2, **kwargs):
        model = kwargs["model"]
        models.append(model)
        runtimes.setdefault(model, SimpleNamespace(detector=Mock(), recognizer=Mock()))
        return runtimes[model]

    monkeypatch.setattr(face_embedding, "_load_cv2", Mock())
    monkeypatch.setattr(face_embedding, "_runtime_for_model", runtime)
    monkeypatch.setattr(face_embedding, "_extract_embedding", Mock(return_value=(1.0,) * 512))
    face_embedding.warm_models()
    assert models == ["sface", ADAFACE_MODEL_NAME]
    assert all(value.detector.detect.called for value in runtimes.values())
    assert face_embedding._extract_embedding.call_count == 2
