from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture
def cdn() -> ModuleType:
    script = Path(__file__).resolve().parents[2] / "deploy/configure-media-cdn.py"
    spec = importlib.util.spec_from_file_location("configure_media_cdn", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_media_cdn_routes_keep_distinct_security_and_cache_contracts(cdn: ModuleType) -> None:
    bucket = "findme-photo-media-delivery-prod-b1g2qttg"
    cover, preview, original, denied = cdn.rules(
        bucket, "123", "456", "token-secret", "origin-secret"
    )
    cover_path = f"/{bucket}/event-covers/12345678-1234-1234-1234-123456789abc"
    for suffix in ("", ".jpg", ".png"):
        assert re.fullmatch(cover["rulePattern"], cover_path + suffix)
    assert not re.fullmatch(cover["rulePattern"], cover_path + "/a.jpg")
    assert not re.fullmatch(cover["rulePattern"], f"/{bucket}/event-covers/../originals/a.jpg")
    assert not re.fullmatch(cover["rulePattern"], f"/{bucket}/previews/a/b/c.jpg")
    assert re.fullmatch(preview["rulePattern"], f"/{bucket}/previews/a/preview-small-v1/c.jpg")
    assert re.fullmatch(original["rulePattern"], "/delivery/originals/" + "a" * 32 + "/inline")
    assert re.fullmatch(
        original["rulePattern"], "/delivery/originals/" + "a" * 32 + "/findme-photo-p.jpg"
    )
    assert re.fullmatch(denied["rulePattern"], f"/{bucket}/originals/a")
    assert [int(rule["weight"]) for rule in (cover, preview, original, denied)] == [
        10,
        20,
        30,
        9999,
    ]
    assert cover["options"]["secureKey"] == {"enabled": False}
    for rule in (cover, preview):
        assert "X-FindMe-Origin-Auth" not in str(rule)
        assert rule["options"]["hostOptions"]["host"]["value"] == "storage.yandexcloud.net"
        assert rule["originsGroupId"] == "123"
    for rule in (preview, original):
        assert rule["options"]["secureKey"]["type"] == "DISABLE_IP_SIGNING"
        assert rule["options"]["queryParamsOptions"]["ignoreQueryString"]["value"] is True
        assert rule["options"]["staticHeaders"]["value"]["Cache-Control"] == "private, no-store"
        assert rule["originProtocol"] == "HTTPS"
    assert original["originsGroupId"] == "456"
    assert original["options"]["staticRequestHeaders"]["value"] == {
        "X-FindMe-Origin-Auth": "origin-secret"
    }
    assert denied["options"]["staticResponse"]["code"] == "404"


class FakeCloud:
    def __init__(self) -> None:
        self.resource: dict[str, Any] = {
            "id": "bc8ryczd5bpuzduhwubz",
            "folderId": "folder",
            "cname": "img.findme-photo.ru",
            "sslCertificate": {"type": "CM", "status": "READY"},
            "active": True,
        }
        self.rules: list[dict[str, Any]] = []
        self.groups: dict[str, str] = {}
        self.mutations: list[str] = []

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if method == "GET":
            return {"rules": self.rules} if path.startswith("/rules?") else self.resource
        assert payload is not None
        if path.startswith("/resources/"):
            self.mutations.append("resource")
            mask = set(payload["updateMask"].split(","))
            assert mask == {
                "originGroupId",
                "originProtocol",
                "options.disableCache",
                "options.secureKey",
                "options.staticRequestHeaders",
                "options.hostOptions",
                "options.allowedHttpMethods",
                "options.ignoreCookie",
                "options.browserCacheSettings",
            }
            assert payload["options"]["browserCacheSettings"] == {"enabled": False}
            self.resource.update(payload)
        else:
            self.mutations.append(payload["name"])
            if method == "POST":
                self.rules.append({"id": str(len(self.rules) + 1), **payload})
            else:
                rule = next(rule for rule in self.rules if rule["id"] == path.split("/")[-1])
                rule.update(payload)
        return {"done": True, "response": {}}

    def origin_group(self, folder: str, host: str, *, apply: bool) -> str:
        assert folder == "folder"
        if apply:
            return self.groups.setdefault(host, str(len(self.groups) + 1))
        return self.groups.get(host, "0")


def test_media_cdn_dry_run_and_repeat_apply(cdn: ModuleType) -> None:
    cloud = FakeCloud()
    plan = cdn.configure(cloud, "test-bucket")
    assert not cloud.mutations
    assert not cloud.groups
    assert plan["mode"] == "dry-run"
    assert "secret" not in str(plan).lower()
    cdn.configure(cloud, "test-bucket", credentials=("token-secret", "origin-secret"))
    assert cloud.mutations == [
        "deny-other-paths",
        "resource",
        "public-covers",
        "private-previews",
        "private-originals",
    ]
    assert cloud.resource["sslCertificate"] == {"type": "CM", "status": "READY"}
    assert cloud.resource["options"]["secureKey"] == {"enabled": False}
    assert cloud.resource["options"]["staticRequestHeaders"] == {"enabled": False}
    cdn.configure(cloud, "test-bucket", credentials=("token-secret", "origin-secret"))
    assert len(cloud.groups) == 2 and len(cloud.rules) == 4


def test_media_cdn_refuses_unknown_rule_before_mutation(cdn: ModuleType) -> None:
    cloud = FakeCloud()
    cloud.rules.append({"id": "1", "name": "unreviewed-rule"})
    with pytest.raises(cdn.ConfigurationError, match="unexpected_rule"):
        cdn.configure(cloud, "test-bucket", credentials=("token-secret", "origin-secret"))
    assert not cloud.groups and not cloud.mutations
