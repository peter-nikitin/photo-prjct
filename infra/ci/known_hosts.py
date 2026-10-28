"""Trust the new VM host key through authenticated Yandex serial output."""

import base64
import json
import os
import re
import sys
import time
from urllib.request import Request, urlopen

MARKER = re.compile(r"FINDME_HOST_ED25519 (ssh-ed25519) ([A-Za-z0-9+/=]+)")


def known_host(serial: str, host: str) -> str:
    if not re.fullmatch(r"[0-9.]+", host) or len(host.split(".")) != 4:
        raise ValueError("expected Terraform's public IPv4 address")
    keys = {
        match.groups()
        for line in serial.splitlines()
        if (match := MARKER.fullmatch(line.rstrip("\r")))
    }
    if len(keys) != 1:
        raise ValueError("missing or conflicting VM host key in authenticated serial output")
    algorithm, encoded = keys.pop()
    key = base64.b64decode(encoded, validate=True)
    if not key.startswith(b"\x00\x00\x00\x0bssh-ed25519"):
        raise ValueError("invalid ED25519 host public key")
    return f"{host} {algorithm} {encoded}\n"


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
