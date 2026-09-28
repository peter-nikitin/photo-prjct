"""Run with the isolated monitoring-tools Python; no cloud requests are made."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(importlib.util.find_spec("yandexcloud"), "isolated monitoring SDK required")
class DashboardTransportTests(unittest.TestCase):
    def test_real_sdk_constructs_monitoring_stub_with_sdk_iam_credentials(self):
        import grpc
        from yandex.cloud.monitoring.v3.dashboard_service_pb2_grpc import DashboardServiceStub
        from yandexcloud._channels import Channels

        spec = importlib.util.spec_from_file_location(
            "monitoring_control_sdk", ROOT / "deploy/monitoring/prometheus/control.py"
        )
        assert spec and spec.loader
        control = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = control
        spec.loader.exec_module(control)
        plugins = []
        real_credentials = grpc.metadata_call_credentials

        def credentials(plugin, name=None):
            plugins.append(plugin)
            return real_credentials(plugin, name)

        # Endpoint discovery is the sole RPC otherwise performed by construction.
        with (
            patch.object(
                Channels, "_get_endpoints", return_value={"iam": "iam.api.cloud.yandex.net:443"}
            ),
            patch.object(grpc, "metadata_call_credentials", side_effect=credentials),
        ):
            transport = control.CloudTransport(control.load_config(), "offline-test-token")
        try:
            self.assertIsInstance(transport.dashboard, DashboardServiceStub)
            self.assertEqual(len(plugins), 1)
            metadata = []
            plugins[0](
                SimpleNamespace(
                    service_url="https://monitoring.api.cloud.yandex.net/yandex.cloud.monitoring.v3.DashboardService"
                ),
                lambda values, error: metadata.append((values, error)),
            )
            self.assertEqual(metadata, [((("authorization", "Bearer offline-test-token"),), None)])
        finally:
            transport.sdk._channels.channel("monitoring").close()


if __name__ == "__main__":
    unittest.main()
