"""Read-only worker discovery, sent through pinned canonical SSH by CI."""

from __future__ import annotations

import ipaddress
import json
import subprocess
import sys
from typing import Any
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

COMPUTE = "https://compute.api.cloud.yandex.net/compute/v1"
METADATA = "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
MAX_BODY = 1_048_576
TIMEOUT = 10


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_bytes(request: Request, *, max_body: int = MAX_BODY) -> bytes:
    # Default TLS context; no environment proxy or redirect can move bearer credentials.
    with build_opener(ProxyHandler({}), RejectRedirects()).open(
        request, timeout=TIMEOUT
    ) as response:
        if response.status != 200:
            raise ValueError("cloud request failed")
        raw = response.read(max_body + 1)
    if len(raw) > max_body:
        raise ValueError("cloud response too large")
    return raw


def request_json(request: Request) -> dict[str, Any]:
    raw = request_bytes(request)
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("cloud response invalid")
    return result


def metadata_token() -> str:
    payload = request_json(Request(METADATA, headers={"Metadata-Flavor": "Google"}))
    token = payload.get("access_token")
    if (
        not isinstance(token, str)
        or not token
        or not token.isascii()
        or any(c.isspace() for c in token)
        or type(payload.get("expires_in")) is not int
        or not 0 < payload["expires_in"] <= 86_400
    ):
        raise ValueError("metadata identity unavailable")
    return token


class CloudReader:
    def __init__(self, token: str):
        self.token = token

    def get(self, path: str, **parameters: str) -> dict[str, Any]:
        return request_json(
            Request(
                f"{COMPUTE}/{path}?{urlencode(parameters)}",
                headers={"Authorization": f"Bearer {self.token}"},
            )
        )

    def pages(self, path: str, key: str, **parameters: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        seen: set[str] = set()
        for _ in range(10):
            payload = self.get(path, **parameters, pageSize="100", pageToken=token)
            if set(payload) - {key, "nextPageToken"}:
                raise ValueError("invalid cloud page envelope")
            items = payload.get(key, [])
            if not isinstance(items, list) or any(not isinstance(row, dict) for row in items):
                raise ValueError("invalid cloud page")
            rows.extend(items)
            token = payload.get("nextPageToken", "")
            if not isinstance(token, str) or len(token) > 1024 or token in seen:
                raise ValueError("invalid cloud pagination")
            if not token:
                return rows
            seen.add(token)
        raise ValueError("cloud pagination incomplete")


def private_address(value):
    address = ipaddress.IPv4Address(value)
    if not any(
        address in ipaddress.IPv4Network(cidr)
        for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    ):
        raise ValueError("private worker address required")
    return str(address)


def validate_ingress(group, canonical):
    ingress = [
        {key: value for key, value in rule.items() if key not in {"id", "description"}}
        for rule in group.get("rules", [])
        if rule.get("direction") == "INGRESS"
    ]
    if len(ingress) != 1:
        raise ValueError("canonical SSH ingress required")
    rule = ingress[0]
    if "protocolNumber" in rule and str(rule.pop("protocolNumber")) != "6":
        raise ValueError("canonical SSH ingress required")
    sources = {
        group_id
        for nic in canonical["networkInterfaces"]
        for group_id in nic.get("securityGroupIds", [])
    }
    if (
        len(sources) != 1
        or rule
        != {
            "direction": "INGRESS",
            "protocolName": "TCP",
            "ports": {"fromPort": "22", "toPort": "22"},
            "securityGroupId": next(iter(sources)),
        }
        or not any(rule.get("direction") == "EGRESS" for rule in group.get("rules", []))
    ):
        raise ValueError("canonical SSH ingress required")


def discover(cloud, *, folder, groups, subnet, security_group):
    if set(groups) != {"bulk", "selfie"} or len(set(groups.values())) != 2:
        raise ValueError("exactly two approved groups required")
    addresses = []
    identities = set()
    for pool, group_id in groups.items():
        group = cloud.get(f"instanceGroups/{group_id}")
        if group.get("folderId") != folder or group.get("labels") != {
            "project": "findme-photo",
            "deployment": "canonical",
            "managed-by": "worker-pools",
            "pool": pool,
        }:
            raise ValueError("unapproved worker group")
        members = [
            row
            for row in cloud.pages(f"instanceGroups/{group_id}/instances", "instances")
            if row.get("status") != "DELETED"
        ]
        if pool == "selfie" and len(members) != 1:
            raise ValueError("exactly one running selfie required")
        if len(members) > 2:
            raise ValueError("worker capacity exceeds approved maximum")
        for member in members:
            if member.get("status") not in {"RUNNING_ACTUAL", "RUNNING_OUTDATED"}:
                raise ValueError("worker group transitioning; retry release after convergence")
            instance_id = member["instanceId"]
            if instance_id in identities:
                raise ValueError("duplicate worker member")
            identities.add(instance_id)
            instance = cloud.get(f"instances/{instance_id}")
            interfaces = instance.get("networkInterfaces", [])
            if (
                instance.get("status") != "RUNNING"
                or instance.get("folderId") != folder
                or len(interfaces) != 1
            ):
                raise ValueError("private worker not running")
            interface = interfaces[0]
            primary = interface.get("primaryV4Address", {})
            if (
                interface.get("subnetId") != subnet
                or interface.get("securityGroupIds") != [security_group]
                or "oneToOneNat" in primary
            ):
                raise ValueError("private worker network mismatch")
            addresses.append(private_address(primary["address"]))
    if len(set(addresses)) != len(addresses):
        raise ValueError("duplicate worker address")
    return sorted(addresses)


def main():
    parameters = json.loads(sys.argv[1])
    canonical_id = parameters.pop("canonical_id")
    cloud = CloudReader(metadata_token())
    security = request_json(
        Request(
            "https://vpc.api.cloud.yandex.net/vpc/v1/securityGroups/"
            + parameters["security_group"],
            headers={"Authorization": "Bearer " + cloud.token},
        )
    )
    if security.get("folderId") != parameters["folder"]:
        raise ValueError("worker security group outside approved folder")
    validate_ingress(security, cloud.get("instances/" + canonical_id))
    addresses = discover(cloud, **parameters)
    keys = ""
    for address in addresses:
        scan = subprocess.run(
            ["ssh-keyscan", "-T", "10", "-t", "ed25519", address],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        keys += scan.stdout
    print(json.dumps({"addresses": addresses, "host_keys": keys}))


if __name__ == "__main__":
    main()
