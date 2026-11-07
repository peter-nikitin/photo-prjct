"""Trust the new VM host key through authenticated Yandex serial output."""

import base64
import ipaddress
import json
import os
import re
import sys
import time
from urllib.request import Request, urlopen

BEGIN = "-----BEGIN SSH HOST KEY KEYS-----"
END = "-----END SSH HOST KEY KEYS-----"
PUBLIC_KEY = re.compile(r"(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) ([A-Za-z0-9+/=]+)(?: .*)?")


def known_host(serial: str, host: str) -> str:
    try:
        ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        raise ValueError("expected Terraform's public IPv4 address") from None
    lines = [line.rstrip("\r") for line in serial.splitlines()]
    begins = [index for index, line in enumerate(lines) if line == BEGIN]
    ends = [index for index, line in enumerate(lines) if line == END]
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        raise ValueError("missing or conflicting VM host key in authenticated serial output")
    block = [line for line in lines[begins[0] + 1 : ends[0]] if line]
    matches = [PUBLIC_KEY.fullmatch(line) for line in block]
    if not block or any(match is None for match in matches):
        raise ValueError("missing or conflicting VM host key in authenticated serial output")
    ed25519_keys = [
        match for match in matches if match is not None and match.group(1) == "ssh-ed25519"
    ]
    if len(ed25519_keys) != 1:
        raise ValueError("missing or conflicting VM host key in authenticated serial output")
    encoded = ed25519_keys[0].group(2)
    key = base64.b64decode(encoded, validate=True)
    if not key.startswith(b"\x00\x00\x00\x0bssh-ed25519"):
        raise ValueError("invalid ED25519 host public key")
    return f"{host} ssh-ed25519 {encoded}\n"


def main() -> None:
    instance_id, host, output = sys.argv[1:4]
    if not re.fullmatch(r"[a-z0-9]{10,50}", instance_id):
        raise ValueError("invalid instance ID")
    token = os.environ["YC_TOKEN"]
    url = (
        f"https://compute.api.cloud.yandex.net/compute/v1/instances/{instance_id}:serialPortOutput"
    )
    for attempt in range(12):
        request = Request(url, headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=30) as response:
            serial = json.load(response)["contents"]
        try:
            line = known_host(serial, host)
        except ValueError as error:
            if "missing or conflicting" not in str(error) or attempt == 11:
                raise
            time.sleep(5)
            continue
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            destination.write(line)
        return


if __name__ == "__main__":
    main()
