"""Cloud probe wire contract; real protobuf checks run in monitoring-tools Python."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ProbeTests(unittest.TestCase):
    def setUp(self):
        load("monitor_public_health", "scripts/monitor_public_health.py")
        self.probe = load("public_probe_function", "deploy/public-probe/index.py")

    def test_success_and_observed_failure_have_fresh_timestamp_and_elapsed_time(self):
        for success, response in (
            (True, self.probe.HealthResponse(status=200, body=b'{"status":"ok"}')),
            (False, self.probe.HealthResponse(status=503, body=b"unavailable")),
            (False, self.probe.HealthResponse(status=200, body=b"not health JSON")),
        ):
            with (
                patch.object(self.probe, "fetch_health", return_value=response),
                patch.object(self.probe.time, "perf_counter", side_effect=[10, 10.75]),
                patch.object(self.probe.time, "time", return_value=1000),
            ):
                result = self.probe.observe()
            self.assertEqual(result.success, success)
            self.assertEqual(result.duration_seconds, 0.75)
            self.assertEqual(result.observed_at, 1000)

    def test_network_and_tls_failures_are_observed_failures(self):
        import ssl
        import urllib.error

        for failure in (TimeoutError(), ssl.SSLError(), urllib.error.URLError("DNS failed")):
            with patch.object(self.probe, "fetch_health", side_effect=failure):
                self.assertFalse(self.probe.observe().success)

    def test_handler_uses_short_lived_context_token_and_does_not_retry_observed_outage(self):
        observation = SimpleNamespace(success=False)
        with (
            patch.object(self.probe, "observe", return_value=observation),
            patch.object(self.probe, "publish") as publish,
            patch.dict(os.environ, {"PROMETHEUS_WORKSPACE_ID": "workspace"}),
        ):
            result = self.probe.handler({}, SimpleNamespace(token={"access_token": "private"}))
        self.assertEqual(result, {"success": False})
        publish.assert_called_once_with(observation, token="private", workspace_id="workspace")

    def test_package_is_reproducible_and_contains_its_runtime_dependencies(self):
        import tempfile

        package = load("public_probe_package", "deploy/public-probe/package.py")
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "a.zip", Path(tmp) / "b.zip"
            package.build(first)
            package.build(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with ZipFile(first) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"index.py", "monitor_public_health.py", "requirements.txt"},
                )
                self.assertEqual(
                    archive.read("index.py"), (ROOT / "deploy/public-probe/index.py").read_bytes()
                )


@unittest.skipUnless(importlib.util.find_spec("remote_pb2"), "probe runtime SDK required")
class ProbeWireTests(unittest.TestCase):
    def setUp(self):
        load("monitor_public_health", "scripts/monitor_public_health.py")
        self.probe = load("public_probe_function_wire", "deploy/public-probe/index.py")

    def test_remote_write_preserves_values_labels_and_timestamp(self):
        import remote_pb2
        import snappy

        with patch.object(self.probe.requests, "post", return_value=Mock(status_code=204)) as post:
            self.probe.publish(
                self.probe.Observation(False, 0.75, 1000), token="private", workspace_id="workspace"
            )
        (url,) = post.call_args.args
        self.assertEqual(
            url,
            "https://monitoring.api.cloud.yandex.net/prometheus/workspaces/workspace/api/v1/write",
        )
        wire = remote_pb2.WriteRequest.FromString(snappy.decompress(post.call_args.kwargs["data"]))
        values = {}
        for series in wire.timeseries:
            labels = {label.name: label.value for label in series.labels}
            metric = labels.pop("__name__")
            self.assertEqual(labels, {"check": "canonical-health", "instance": "findme-photo-app"})
            self.assertEqual(series.samples[0].timestamp, 1_000_000)
            values[metric] = series.samples[0].value
        self.assertEqual(
            values,
            {
                "findme_probe_success": 0,
                "findme_probe_duration_seconds": 0.75,
                "findme_probe_observed_timestamp_seconds": 1000,
            },
        )

    def test_publication_failure_does_not_expose_token_or_response_body(self):
        with patch.object(
            self.probe.requests, "post", return_value=Mock(status_code=403, text="private-body")
        ):
            with self.assertRaises(RuntimeError) as error:
                self.probe.publish(
                    self.probe.Observation(True, 0.2, 1000),
                    token="private-token",
                    workspace_id="workspace",
                )
        self.assertNotIn("private", str(error.exception))

    def test_publication_timeout_fails_the_invocation_without_faking_probe_state(self):
        import requests

        with (
            patch.object(
                self.probe, "observe", return_value=self.probe.Observation(True, 0.2, 1000)
            ),
            patch.object(
                self.probe.requests, "post", side_effect=requests.Timeout("private-token")
            ),
            patch.dict(os.environ, {"PROMETHEUS_WORKSPACE_ID": "workspace"}),
        ):
            with self.assertRaises(RuntimeError) as error:
                self.probe.handler({}, SimpleNamespace(token={"access_token": "private-token"}))
        self.assertNotIn("private", str(error.exception))
