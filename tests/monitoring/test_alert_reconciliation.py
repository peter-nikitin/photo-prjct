import base64
import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location(
        "alert_reconciliation", ROOT / "deploy/monitoring/prometheus/alerts.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Transport:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def rule_file(self, name):
        self.calls.append(("get", name))
        return {"name": name, "content": base64.b64encode(self.content).decode()}

    def put_rule_file(self, name, content):
        self.calls.append(("put", name))
        self.content = content

    def rule_snapshots(self, name):
        self.calls.append(("snapshots", name))
        groups = yaml.safe_load(self.content)["groups"]
        return {
            "snapshotByGroup": {
                group["name"]: [
                    {
                        "state": "OK",
                        "record": rule["alert"],
                        "evaluatedAtTimeEpochMs": int(time.time() * 1000) + 1000,
                    }
                    for rule in group["rules"]
                ]
                for group in groups
            }
        }

    def read_samples(self, selectors, *, now=None):
        self.calls.append(("read_samples",))
        return [[[(now - 60, now - 60)]] for _ in selectors]


def test_changed_rule_file_is_backed_up_then_read_back(tmp_path):
    module = load()
    transport = Transport(b"old rules")
    desired = (module.HERE / "rules.yml").read_bytes()
    module.reconcile_rules(transport, desired, tmp_path / "backup")
    assert (tmp_path / "backup/findme-photo.yml").read_bytes() == b"old rules"
    assert transport.calls == [
        ("get", "findme-photo.yml"),
        ("put", "findme-photo.yml"),
        ("get", "findme-photo.yml"),
        ("snapshots", "findme-photo.yml"),
    ]


def test_matching_rule_file_is_not_rewritten(tmp_path):
    module = load()
    desired = (module.HERE / "rules.yml").read_bytes()
    transport = Transport(desired)
    module.reconcile_rules(transport, desired, tmp_path / "backup")
    assert transport.calls == [("get", "findme-photo.yml"), ("snapshots", "findme-photo.yml")]
    assert not (tmp_path / "backup").exists()


def test_failed_readback_preserves_backup(tmp_path):
    module = load()
    transport = Transport(b"old rules")
    transport.put_rule_file = lambda name, content: transport.calls.append(("put", name))
    with pytest.raises(module.control.ControlError, match="read-back"):
        module.reconcile_rules(
            transport, (module.HERE / "rules.yml").read_bytes(), tmp_path / "backup"
        )
    assert (tmp_path / "backup/findme-photo.yml").read_bytes() == b"old rules"


def test_documented_rule_file_put_uses_workspace_extensions_api():
    module = load()
    transport = module.control.CloudTransport.__new__(module.control.CloudTransport)
    transport.config = {"workspace_id": "workspace-one"}
    transport.token = "private-token"
    transport.session = Mock()
    transport.session.request.return_value = SimpleNamespace(status_code=204)
    transport.put_rule_file("findme-photo.yml", b"groups: []")
    args, kwargs = transport.session.request.call_args
    assert args == (
        "PUT",
        "https://monitoring.api.cloud.yandex.net/prometheus/workspaces/"
        "workspace-one/extensions/v1/rules",
    )
    assert kwargs["json"] == {
        "name": "findme-photo.yml",
        "content": base64.b64encode(b"groups: []").decode(),
    }
    assert kwargs["headers"]["Authorization"] == "Bearer private-token"
    assert kwargs["timeout"] == 30


def test_extensions_failure_does_not_expose_response_body():
    module = load()
    transport = module.control.CloudTransport.__new__(module.control.CloudTransport)
    transport.config = {"workspace_id": "workspace-one"}
    transport.token = "private-token"
    transport.session = Mock()
    transport.session.request.return_value = SimpleNamespace(
        status_code=403, text="private-token secret response"
    )
    with pytest.raises(module.control.ControlError) as error:
        transport.put_rule_file("findme-photo.yml", b"groups: []")
    assert "403" in str(error.value)
    assert "private-token" not in str(error.value)


def test_preflight_accepts_fresh_observations():
    module = load()
    transport = Transport(b"old rules")
    module.check_sources(transport, now=1000)


def test_partial_snapshot_does_not_validate_matching_rule_file(tmp_path, monkeypatch):
    module = load()
    desired = (module.HERE / "rules.yml").read_bytes()
    transport = Transport(desired)
    original = transport.rule_snapshots

    def partial(name):
        full = original(name)
        full["snapshotByGroup"]["findme-critical"] = full["snapshotByGroup"]["findme-critical"][:1]
        return full

    transport.rule_snapshots = partial
    monkeypatch.setattr(module.time, "monotonic", Mock(side_effect=[0, 361]))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    with pytest.raises(module.control.ControlError, match="evaluation"):
        module.reconcile_rules(transport, desired, tmp_path / "backup")
    assert transport.calls == [("get", "findme-photo.yml"), ("snapshots", "findme-photo.yml")]


def test_fresh_preflight_rejects_future_observation_value():
    module = load()
    transport = Transport(b"old rules")

    def samples(selectors, now=None):
        return [
            [
                [
                    (
                        now - 60,
                        now + 10
                        if selector["__name__"] == "findme_queue_observed_timestamp_seconds"
                        else now - 60,
                    )
                ]
            ]
            for selector in selectors
        ]

    transport.read_samples = samples
    with pytest.raises(module.MissingSource, match="findme_queue_observed"):
        module.check_sources(transport, now=1000)


def test_preflight_protocol_error_is_not_retried(monkeypatch):
    module = load()
    transport = Transport(b"old rules")
    transport.read_samples = Mock(side_effect=module.control.ControlError("Monitoring read failed"))
    monkeypatch.setattr(module.time, "sleep", Mock(side_effect=AssertionError("must not retry")))
    with pytest.raises(module.control.ControlError, match="Monitoring read failed"):
        module.wait_for_sources(transport)
    assert transport.read_samples.call_count == 1


def test_missing_gallery_search_observation_blocks_publication(tmp_path, monkeypatch):
    module = load()
    transport = Transport(b"old rules")
    read_samples = transport.read_samples
    missing_selector = {
        "__name__": "findme_queue_observed_timestamp_seconds",
        "queue": "gallery-search",
        "instance": "findme-photo-app",
    }

    def samples_without_gallery_search(selectors, *, now=None):
        fresh_samples = read_samples(selectors, now=now)
        return [
            [] if selector == missing_selector else samples
            for selector, samples in zip(selectors, fresh_samples, strict=True)
        ]

    monkeypatch.setattr(transport, "read_samples", samples_without_gallery_search)
    monkeypatch.setattr(module.time, "monotonic", Mock(side_effect=[0, 601]))
    with pytest.raises(module.control.ControlError, match="old rules retained"):
        module.publish_alerts(
            transport, (module.HERE / "rules.yml").read_bytes(), b"route", tmp_path / "backup"
        )
    assert transport.calls == [("read_samples",)]
