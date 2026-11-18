from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://cdn.api.cloud.yandex.net/cdn/v1"
RESOURCE = "bc8ryczd5bpuzduhwubz"
CNAME = "img.findme-photo.ru"
STORAGE = "storage.yandexcloud.net"
WEB = "findme-photo.ru"
GROUPS = {STORAGE: "findme-cdn-object-storage", WEB: "findme-cdn-originals"}
HEADER = "X-FindMe-Origin-Auth"


class ConfigurationError(Exception):
    pass


def yc(*args: str) -> str:
    result = subprocess.run(["yc", *args], capture_output=True, text=True, timeout=300, check=False)
    if result.returncode:
        raise ConfigurationError("provider_command_failed")
    return result.stdout.strip()


class Cloud:
    def __init__(self) -> None:
        self.token = yc("iam", "create-token")
        if not self.token:
            raise ConfigurationError("missing_iam_token")

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request = Request(
            API + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            raise ConfigurationError(f"provider_http_{error.code}") from None
        except (URLError, ValueError, TimeoutError):
            raise ConfigurationError("provider_request_failed") from None

    def origin_group(self, folder: str, host: str, *, apply: bool) -> str:
        name = GROUPS[host]
        groups = json.loads(
            yc("cdn", "origin-group", "list", "--folder-id", folder, "--format", "json")
        )
        matches = [group for group in groups if group["name"] == name]
        if len(matches) > 1:
            raise ConfigurationError("ambiguous_origin_group")
        if matches:
            group = matches[0]
            origins = group["origins"]
            if len(origins) != 1 or any(
                origin["source"] != host or not origin.get("enabled") or origin.get("backup")
                for origin in origins
            ):
                raise ConfigurationError("origin_group_drift")
        elif apply:
            group = json.loads(
                yc(
                    "cdn",
                    "origin-group",
                    "create",
                    "--folder-id",
                    folder,
                    "--name",
                    name,
                    "--origin",
                    f"source={host},enabled=true,backup=false",
                    "--format",
                    "json",
                )
            )
        else:
            return "0"
        group_id = str(group["id"])
        if not re.fullmatch(r"[1-9][0-9]*", group_id):
            raise ConfigurationError("invalid_origin_group")
        return group_id


def rules(
    bucket: str, storage_id: str, web_id: str, token: str, header: str
) -> list[dict[str, Any]]:
    bucket_path = bucket.replace(".", r"\.")
    # The edge pattern must fit the provider's limit with the production bucket name.
    # Only the cover prefix is public; S3 also enforces each object's ACL.
    cover = rf"^/{bucket_path}/event-covers/[a-f0-9-]{{36}}(\.[a-z0-9]{{1,10}})?$"
    preview = rf"^/{bucket_path}/previews/[\w-]+/[\w-]+/[a-f0-9-]+\.jpg$"
    original = (
        r"^/delivery/originals/[0-9a-f]{32}/(inline|findme-photo-[A-Za-z0-9_-]{1,32}\.(jpg|png))$"
    )
    if len(cover) > 100 or len(preview) > 100:
        raise ConfigurationError("bucket_name_exceeds_rule_pattern_limit")
    cache = {
        "disableCache": {"enabled": True, "value": False},
        "edgeCacheSettings": {
            "enabled": True,
            "value": {"customValues": {"200": "2592000", "206": "2592000"}},
        },
        # This option changes the cache key. It leaves the SigV4 query on the origin request.
        "queryParamsOptions": {"ignoreQueryString": {"enabled": True, "value": True}},
    }
    private = {
        "secureKey": {"enabled": True, "type": "DISABLE_IP_SIGNING", "key": token},
        "staticHeaders": {"enabled": True, "value": {"Cache-Control": "private, no-store"}},
    }
    storage_options = {
        **cache,
        "hostOptions": {"host": {"enabled": True, "value": STORAGE}},
        "staticRequestHeaders": {"enabled": False},
    }
    return [
        rule(
            "public-covers",
            cover,
            "10",
            storage_id,
            {
                **storage_options,
                "secureKey": {"enabled": False},
            },
        ),
        rule("private-previews", preview, "20", storage_id, {**storage_options, **private}),
        rule(
            "private-originals",
            original,
            "30",
            web_id,
            {
                **cache,
                **private,
                "hostOptions": {"host": {"enabled": True, "value": WEB}},
                "staticRequestHeaders": {"enabled": True, "value": {HEADER: header}},
            },
        ),
        rule(
            "deny-other-paths",
            "^/.*",
            "9999",
            "0",
            {
                "disableCache": {"enabled": True, "value": True},
                "staticResponse": {"enabled": True, "code": "404", "content": "Not Found"},
            },
        ),
    ]


def rule(
    name: str, pattern: str, weight: str, group: str, options: dict[str, Any]
) -> dict[str, Any]:
    return {
        "name": name,
        "resourceId": RESOURCE,
        "rulePattern": pattern,
        "weight": weight,
        "originsGroupId": group,
        "originProtocol": "HTTPS",
        "options": options,
    }


def wait(operation: dict[str, Any]) -> None:
    if "error" in operation:
        raise ConfigurationError("provider_operation_failed")
    if operation.get("done") is True:
        if "response" not in operation:
            raise ConfigurationError("provider_operation_invalid")
        return
    operation_id = operation.get("id", "")
    if not isinstance(operation_id, str) or not re.fullmatch(r"[a-z0-9]+", operation_id):
        raise ConfigurationError("provider_operation_invalid")
    yc("operation", "wait", operation_id, "--format", "json")


def configure(
    cloud: Cloud, bucket: str, credentials: tuple[str, str] | None = None
) -> dict[str, Any]:
    resource_path = f"/resources/{RESOURCE}"
    resource = cloud.request("GET", resource_path)
    cert = resource.get("sslCertificate") or {}
    if resource.get("id") != RESOURCE or resource.get("cname") != CNAME:
        raise ConfigurationError("unexpected_cdn_resource")
    if cert.get("type") != "CM" or cert.get("status") != "READY":
        raise ConfigurationError("cdn_certificate_not_ready")

    existing = cloud.request("GET", f"/rules?{urlencode({'resourceId': RESOURCE})}")
    placeholders = rules(bucket, "0", "0", "", "")
    managed = {rule["name"] for rule in placeholders}
    current: dict[str, str] = {}
    for rule_state in existing.get("rules", []):
        name = rule_state["name"]
        rule_id = str(rule_state["id"])
        if name not in managed or name in current or not re.fullmatch(r"[1-9][0-9]*", rule_id):
            raise ConfigurationError("unexpected_rule")
        current[name] = rule_id

    apply = credentials is not None
    folder = resource["folderId"]
    storage_id = cloud.origin_group(folder, STORAGE, apply=apply)
    web_id = cloud.origin_group(folder, WEB, apply=apply)
    if not apply:
        return {
            "mode": "dry-run",
            "cdn": CNAME,
            "resource": RESOURCE,
            "active": resource.get("active", False),
            "root": "HTTPS; remove legacy signing and request headers",
            "origins": {
                host: {"name": GROUPS[host], "id": group_id if group_id != "0" else None}
                for host, group_id in ((STORAGE, storage_id), (WEB, web_id))
            },
            "rules": [
                {
                    "name": item["name"],
                    "pattern": item["rulePattern"],
                    "weight": item["weight"],
                    "origin": {
                        "public-covers": STORAGE,
                        "private-previews": STORAGE,
                        "private-originals": WEB,
                    }.get(item["name"]),
                    "signed": item["options"].get("secureKey", {}).get("enabled", False),
                    "action": "update" if item["name"] in current else "create",
                }
                for item in placeholders
            ],
        }

    cover, preview, original, denied = rules(bucket, storage_id, web_id, *credentials)
    put_rule(cloud, current, denied)
    root_options = {
        "disableCache": {"enabled": True, "value": True},
        "secureKey": {"enabled": False},
        "staticRequestHeaders": {"enabled": False},
        "hostOptions": {"host": {"enabled": True, "value": WEB}},
        "allowedHttpMethods": {"enabled": True, "value": ["GET", "HEAD"]},
        "ignoreCookie": {"enabled": True, "value": True},
        "browserCacheSettings": {"enabled": False},
    }
    wait(
        cloud.request(
            "PATCH",
            resource_path,
            {
                "updateMask": ",".join(
                    [
                        "originGroupId",
                        "originProtocol",
                        *(f"options.{name}" for name in root_options),
                    ]
                ),
                "originGroupId": web_id,
                "originProtocol": "HTTPS",
                "options": root_options,
            },
        )
    )
    for item in (cover, preview, original):
        put_rule(cloud, current, item)
    return {"mode": "applied", "cdn": CNAME, "resource": RESOURCE}


def put_rule(cloud: Cloud, current: dict[str, str], body: dict[str, Any]) -> None:
    rule_id = current.get(body["name"])
    if rule_id is None:
        wait(cloud.request("POST", "/rules", body))
    else:
        wait(cloud.request("PATCH", f"/rules/{rule_id}", body))


def projection() -> tuple[str, str]:
    path = Path(os.environ["FINDME_ENV_FILE"])
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ConfigurationError("projection_not_private")
    names = {"GALLERY_CDN_TOKEN_SECRET", "IMAGE_ORIGIN_HEADER_SECRET"}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if not separator or name not in names or name in values:
            raise ConfigurationError("invalid_projection")
        values[name] = json.loads(value)
    token = values.get("GALLERY_CDN_TOKEN_SECRET", "")
    header = values.get("IMAGE_ORIGIN_HEADER_SECRET", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", token):
        raise ConfigurationError("invalid_cdn_token")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", header):
        raise ConfigurationError("invalid_origin_header")
    return token, header


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Configure direct media delivery on img.findme-photo.ru"
    )
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", args.bucket):
            raise ConfigurationError("invalid_bucket")
        credentials = projection() if args.apply else None
        result = configure(Cloud(), args.bucket, credentials)
    except ConfigurationError as error:
        print(f"configure-media-cdn: {error}", file=sys.stderr)
        return 2
    except (KeyError, OSError, TypeError, ValueError, subprocess.TimeoutExpired):
        print("configure-media-cdn: invalid_provider_response_or_projection", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
