"""Register the VM's SSH ED25519 host key after authenticated verification."""

import ipaddress
import json
import os
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

API = "https://compute.api.cloud.yandex.net/compute/v1"
OPERATION_API = "https://operation.api.cloud.yandex.net/operations"
METADATA_KEY = "findme-ssh-host-key"


def request_json(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Yandex Compute API request failed: {method} {url}") from error


def _fingerprints(public_keys: str) -> list[str]:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as key_file:
        key_file.write(public_keys.replace("\r\n", "\n"))
        key_file.flush()
        result = subprocess.run(
            ["ssh-keygen", "-l", "-E", "sha256", "-f", key_file.name],
            check=False,
            capture_output=True,
            text=True,
        )
    if result.returncode:
        raise ValueError("OpenSSH could not read host public keys")
    fingerprints = [
        fields[1]
        for line in result.stdout.splitlines()
        if (fields := line.split())
        and len(fields) >= 4
        and fields[-1] == "(ED25519)"
        and fields[1].startswith("SHA256:")
    ]
    return fingerprints


def run_keyscan(host: str) -> str:
    try:
        result = subprocess.run(
            ["ssh-keyscan", "-4", "-T", "10", "-t", "ed25519", host],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("SSH host key discovery failed") from error
    if result.returncode:
        raise RuntimeError("SSH host key discovery failed")
    lines = [line for line in result.stdout.splitlines() if line and not line.startswith("#")]
    if len(lines) != 1:
        raise ValueError("expected exactly one SSH ED25519 host key")
    fields = lines[0].split()
    if len(fields) < 3 or fields[0] != host or fields[1] != "ssh-ed25519":
        raise ValueError("ssh-keyscan returned an unexpected host key")
    return " ".join(fields[1:])


def discover_host_key(
    instance_id: str,
    host: str,
    token: str,
    *,
    request=None,
    keyscan=None,
) -> str:
    request = request or request_json
    keyscan = keyscan or run_keyscan
    if str(ipaddress.IPv4Address(host)) != host:
        raise ValueError("expected VM public IPv4 address")
    serial_url = f"{API}/instances/{instance_id}:serialPortOutput"
    serial = request("GET", serial_url, token).get("contents", "")
    scanned_key = keyscan(host)
    try:
        serial_fingerprints = _fingerprints(serial)
    except ValueError as error:
        raise RuntimeError("authenticated serial host key is not ready") from error
    if not serial_fingerprints:
        raise RuntimeError("authenticated serial host key is not ready")
    if len(serial_fingerprints) != 1:
        raise ValueError("authenticated serial output has conflicting ED25519 host keys")
    if serial_fingerprints != _fingerprints(scanned_key):
        raise ValueError("SSH host key fingerprint mismatch with authenticated serial output")
    return scanned_key


def _public_host(instance: dict) -> str:
    try:
        return instance["networkInterfaces"][0]["primaryV4Address"]["oneToOneNat"]["address"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("VM does not have a public IPv4 address") from error


def register_vm_ssh(
    instance_id: str,
    token: str,
    *,
    request=request_json,
    keyscan=run_keyscan,
    sleep=time.sleep,
) -> str:
    instance_url = f"{API}/instances/{instance_id}?view=FULL"
    instance = request("GET", instance_url, token)
    if METADATA_KEY in instance.get("metadata", {}):
        raise ValueError("VM SSH host key is already registered")

    for attempt in range(60):
        if instance.get("status") == "RUNNING":
            break
        if attempt == 59:
            raise TimeoutError("VM did not become ready for SSH host key registration")
        sleep(5)
        instance = request("GET", instance_url, token)
        if METADATA_KEY in instance.get("metadata", {}):
            raise ValueError("VM SSH host key is already registered")

    host = _public_host(instance)
    key = ""
    for attempt in range(60):
        try:
            key = discover_host_key(instance_id, host, token, request=request, keyscan=keyscan)
            break
        except RuntimeError as error:
            if attempt == 59:
                raise TimeoutError("SSH host key did not become ready") from error
            sleep(5)

    operation = request(
        "POST",
        f"{API}/instances/{instance_id}/updateMetadata",
        token,
        {"upsert": {METADATA_KEY: key}},
    )
    operation_id = operation.get("id")
    if not operation_id:
        raise RuntimeError("Yandex Compute API returned no metadata update operation")
    for attempt in range(60):
        status = request("GET", f"{OPERATION_API}/{operation_id}", token)
        if status.get("done"):
            if status.get("error"):
                raise RuntimeError("VM SSH host key metadata update failed")
            return key
        if attempt < 59:
            sleep(5)
    raise TimeoutError("VM SSH host key metadata update did not finish")


def main() -> None:
    instance_id = sys.argv[1]
    token = os.environ["YC_TOKEN"]
    register_vm_ssh(instance_id, token)


if __name__ == "__main__":
    main()
