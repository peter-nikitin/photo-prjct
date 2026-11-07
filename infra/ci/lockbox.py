"""Fetch and install the CI deploy SSH key without printing its payload."""

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def _public_key(private_key: str) -> str:
    descriptor, temp_name = tempfile.mkstemp(prefix="findme-lockbox-key-")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as key_file:
            key_file.write(private_key)
        result = subprocess.run(
            ["ssh-keygen", "-y", "-f", temp_name],
            input="",
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return " ".join(result.stdout.split()[:2])
    finally:
        Path(temp_name).unlink(missing_ok=True)


def write_private_key(
    payload: dict,
    *,
    expected_user: str,
    expected_public_key: str,
    target: Path,
    derive_public_key: Callable[[str], str] = _public_key,
) -> None:
    if not isinstance(payload, dict):
        raise ValueError("credentials do not match the reviewed deploy identity")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("credentials do not match the reviewed deploy identity")
    values: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"key", "textValue"}:
            raise ValueError("credentials do not match the reviewed deploy identity")
        key = entry["key"]
        value = entry["textValue"]
        if not isinstance(key, str) or key in values or not isinstance(value, str):
            raise ValueError("credentials do not match the reviewed deploy identity")
        values[key] = value
    if set(values) != {"ssh_private_key", "ssh_public_key", "ssh_user"}:
        raise ValueError("credentials do not match the reviewed deploy identity")
    expected = " ".join(expected_public_key.split()[:2])
    stored = " ".join(values["ssh_public_key"].split()[:2])
    if values["ssh_user"] != expected_user or stored != expected:
        raise ValueError("credentials do not match the reviewed deploy identity")
    if "\x00" in values["ssh_private_key"] or not values["ssh_private_key"].strip():
        raise ValueError("credentials do not match the reviewed deploy identity")
    try:
        derived = " ".join(derive_public_key(values["ssh_private_key"]).split()[:2])
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ValueError("credentials do not match the reviewed deploy identity") from None
    if derived != expected:
        raise ValueError("credentials do not match the reviewed deploy identity")

    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as key_file:
            key_file.write(values["ssh_private_key"])
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def fetch_payload(secret_id: str, token: str) -> dict:
    if not re.fullmatch(r"[a-z0-9]{20}", secret_id) or not token:
        raise ValueError("invalid Lockbox request")
    request = Request(
        f"https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/{secret_id}/payload",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
            if not isinstance(payload, dict):
                raise ValueError("invalid Lockbox payload")
            return payload
    except (OSError, URLError, json.JSONDecodeError):
        raise ValueError("unable to read the deploy credentials from Lockbox") from None


def main() -> None:
    secret_id, username, public_key, target_name = sys.argv[1:5]
    try:
        payload = fetch_payload(secret_id, os.environ["YC_TOKEN"])
        write_private_key(
            payload,
            expected_user=username,
            expected_public_key=public_key,
            target=Path(target_name),
        )
    except (KeyError, OSError, ValueError):
        raise SystemExit("Unable to prepare the reviewed deploy SSH credential") from None
    print("Deploy SSH credential prepared")


if __name__ == "__main__":
    main()
