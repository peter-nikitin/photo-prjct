"""Critical contracts for first registration of a Terraform VM host key."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_registrar():
    spec = importlib.util.spec_from_file_location(
        "register_vm_ssh", ROOT / "infra/ci/register_vm_ssh.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def generated_key(tmp_path: Path, name: str, algorithm: str) -> str:
    path = tmp_path / name
    command = ["ssh-keygen", "-q", "-t", algorithm, "-N", "", "-f", str(path)]
    if algorithm == "rsa":
        command[4:4] = ["-b", "2048"]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return path.with_suffix(".pub").read_text(encoding="utf-8")


def serial_output(*public_keys: str) -> str:
    return (
        "cloud-init startup noise\r\n"
        "-----BEGIN SSH HOST KEY KEYS-----\r\n"
        + "".join(public_keys)
        + "-----END SSH HOST KEY KEYS-----\r\n"
        "more serial output after the key block\r\n"
    )


def fake_instance(*, status: str = "RUNNING", metadata: dict | None = None) -> dict:
    return {
        "id": "fv4exampleinstance00001",
        "status": status,
        "metadata": metadata or {"user-data": "cloud-init config", "enable-oslogin": "false"},
        "networkInterfaces": [
            {
                "primaryV4Address": {
                    "address": "10.0.0.7",
                    "oneToOneNat": {"address": "192.0.2.7"},
                }
            }
        ],
    }


def test_discovery_uses_ssh_keygen_for_noisy_multikey_serial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registrar = load_registrar()
    ed25519 = generated_key(tmp_path, "ed25519", "ed25519")
    ecdsa = generated_key(tmp_path, "ecdsa", "ecdsa")
    rsa = generated_key(tmp_path, "rsa", "rsa")
    serial = serial_output(ecdsa, ed25519, rsa)
    requests: list[tuple[str, str, dict | None]] = []

    def request_json(method: str, url: str, token: str, body: dict | None = None) -> dict:
        assert token == "test-token"
        requests.append((method, url, body))
        return {"contents": serial}

    def keyscan(host: str) -> str:
        assert host == "192.0.2.7"
        key_type, key_data, *_ = ed25519.split()
        return f"{key_type} {key_data}"

    monkeypatch.setattr(registrar, "request_json", request_json)
    monkeypatch.setattr(registrar, "run_keyscan", keyscan)

    assert registrar.discover_host_key("fv4exampleinstance00001", "192.0.2.7", "test-token") == (
        " ".join(ed25519.split()[:2])
    )
    assert len(requests) == 1
    assert requests[0][0] == "GET"
    assert requests[0][1].endswith(":serialPortOutput")


def test_discovery_normalizes_crlf_host_keys_before_ssh_keygen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registrar = load_registrar()
    ed25519 = generated_key(tmp_path, "crlf-ed25519", "ed25519")
    serial = serial_output(ed25519.replace("\n", "\r\n"))
    original_run = subprocess.run
    ssh_keygen_inputs: list[str] = []

    def capture_ssh_keygen_input(command, *args, **kwargs):
        if command[0] == "ssh-keygen":
            ssh_keygen_inputs.append(Path(command[-1]).read_bytes().decode("utf-8"))
        return original_run(command, *args, **kwargs)

    def request_json(method: str, url: str, token: str, body: dict | None = None) -> dict:
        return {"contents": serial}

    def keyscan(host: str) -> str:
        key_type, key_data, *_ = ed25519.split()
        return f"{key_type} {key_data}"

    monkeypatch.setattr(registrar.subprocess, "run", capture_ssh_keygen_input)
    key = registrar.discover_host_key(
        "fv4exampleinstance00001",
        "192.0.2.7",
        "test-token",
        request=request_json,
        keyscan=keyscan,
    )

    assert key == " ".join(ed25519.split()[:2])
    assert ssh_keygen_inputs
    assert all("\r" not in content for content in ssh_keygen_inputs)


def test_mismatched_fingerprint_fails_without_metadata_write(tmp_path: Path) -> None:
    registrar = load_registrar()
    serial_key = generated_key(tmp_path, "serial", "ed25519")
    scanned_key = generated_key(tmp_path, "scan", "ed25519")
    serial = serial_output(serial_key)
    calls: list[tuple[str, str, dict | None]] = []

    def request_json(method: str, url: str, token: str, body: dict | None = None) -> dict:
        calls.append((method, url, body))
        if url.endswith("?view=FULL"):
            return fake_instance()
        if url.endswith("serialPortOutput"):
            return {"contents": serial}
        raise AssertionError(f"unexpected request: {method} {url}")

    def keyscan(host: str) -> str:
        key_type, key_data, *_ = scanned_key.split()
        return f"{key_type} {key_data}"

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        registrar.register_vm_ssh(
            "fv4exampleinstance00001",
            "test-token",
            request=request_json,
            keyscan=keyscan,
            sleep=lambda _: None,
        )
    assert not any(method == "POST" for method, _, _ in calls)


def test_registration_waits_for_running_and_preserves_metadata(tmp_path: Path) -> None:
    registrar = load_registrar()
    ed25519 = generated_key(tmp_path, "ed25519", "ed25519")
    serial = serial_output(ed25519)
    calls: list[tuple[str, str, dict | None]] = []
    get_count = 0
    operation_count = 0
    serial_count = 0

    def request_json(method: str, url: str, token: str, body: dict | None = None) -> dict:
        nonlocal get_count, operation_count, serial_count
        calls.append((method, url, body))
        if url.endswith("?view=FULL"):
            get_count += 1
            state = "STARTING" if get_count == 1 else "RUNNING"
            return fake_instance(status=state)
        if url.endswith("serialPortOutput"):
            serial_count += 1
            return {"contents": "" if serial_count == 1 else serial}
        if url.endswith("/updateMetadata"):
            assert method == "POST"
            return {"id": "operation-1", "done": False}
        if url.endswith("/operations/operation-1"):
            operation_count += 1
            return {"id": "operation-1", "done": operation_count == 2}
        raise AssertionError(f"unexpected request: {method} {url}")

    def keyscan(host: str) -> str:
        key_type, key_data, *_ = ed25519.split()
        return f"{key_type} {key_data}"

    saved = registrar.register_vm_ssh(
        "fv4exampleinstance00001",
        "test-token",
        request=request_json,
        keyscan=keyscan,
        sleep=lambda _: None,
    )

    assert saved == " ".join(ed25519.split()[:2])
    metadata_update = next(body for method, url, body in calls if method == "POST")
    assert metadata_update == {"upsert": {"findme-ssh-host-key": saved}}
    assert "user-data" not in metadata_update["upsert"]
    assert get_count == 2
    assert serial_count == 2
    assert operation_count == 2


def test_existing_registration_fails_before_serial_or_keyscan() -> None:
    registrar = load_registrar()
    calls: list[str] = []

    def request_json(method: str, url: str, token: str, body: dict | None = None) -> dict:
        calls.append(url)
        return fake_instance(metadata={"findme-ssh-host-key": "ssh-ed25519 bad"})

    def keyscan(host: str) -> str:
        raise AssertionError("registered instances must not be rediscovered")

    with pytest.raises(ValueError, match="already registered"):
        registrar.register_vm_ssh(
            "fv4exampleinstance00001", "test-token", request=request_json, keyscan=keyscan
        )
    assert len(calls) == 1
    assert calls[0].endswith("?view=FULL")
