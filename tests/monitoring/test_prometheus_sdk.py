"""Run with the isolated monitoring-tools Python; no cloud requests are made."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(importlib.util.find_spec("yandexcloud"), "isolated monitoring SDK required")
class DashboardTransportTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "monitoring_control_sdk", ROOT / "deploy/monitoring/prometheus/control.py"
        )
        assert spec and spec.loader
        self.control = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.control
        spec.loader.exec_module(self.control)

    def test_real_sdk_constructs_monitoring_stub_with_sdk_iam_credentials(self):
        import grpc
        from yandex.cloud.monitoring.v3.dashboard_service_pb2_grpc import DashboardServiceStub
        from yandexcloud._channels import Channels

        control = self.control
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

    def update_transport(self, operation):
        transport = self.control.CloudTransport.__new__(self.control.CloudTransport)
        transport.dashboard = SimpleNamespace(Update=Mock(return_value=operation))
        transport.sdk = SimpleNamespace(wait_operation_and_get_result=Mock(return_value=None))
        return transport

    def test_dashboard_update_accepts_completed_operation_without_global_polling(self):
        from yandex.cloud.monitoring.v3.dashboard_pb2 import Dashboard
        from yandex.cloud.operation.operation_pb2 import Operation

        operation = Operation(id="mon-synchronous", done=True)
        operation.response.Pack(Dashboard(id="dashboard1", etag="3"))
        transport = self.update_transport(operation)
        transport.dashboard_update({"dashboardId": "dashboard1", "etag": "2"})
        transport.sdk.wait_operation_and_get_result.assert_not_called()
        request = transport.dashboard.Update.call_args.args[0]
        self.assertEqual(request.dashboard_id, "dashboard1")
        self.assertEqual(request.etag, "2")
        self.assertEqual(transport.dashboard.Update.call_args.kwargs, {"timeout": 30})

    def test_dashboard_update_rejects_completed_operation_error_without_polling(self):
        from google.rpc.status_pb2 import Status
        from yandex.cloud.operation.operation_pb2 import Operation

        operation = Operation(
            id="mon-error", done=True, error=Status(code=3, message="private-detail")
        )
        transport = self.update_transport(operation)
        with self.assertRaises(self.control.ControlError) as error:
            transport.dashboard_update({"dashboardId": "dashboard1", "etag": "2"})
        self.assertNotIn("private-detail", str(error.exception))
        transport.sdk.wait_operation_and_get_result.assert_not_called()

    def test_dashboard_update_rejects_pending_operation_without_polling(self):
        from yandex.cloud.operation.operation_pb2 import Operation

        transport = self.update_transport(Operation(id="mon-pending", done=False))
        with self.assertRaises(self.control.ControlError):
            transport.dashboard_update({"dashboardId": "dashboard1", "etag": "2"})
        transport.sdk.wait_operation_and_get_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()
