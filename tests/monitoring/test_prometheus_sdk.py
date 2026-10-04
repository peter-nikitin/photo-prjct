"""Run with the isolated monitoring-tools Python; no cloud requests are made."""

import copy
import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
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

    def test_package_accepts_mixed_prometheus_and_monitoring_sdk_targets(self):
        package = self.control.render(self.control.load_config())
        self.validate_dashboard(package)

    def test_grouped_dashboard_survives_real_sdk_readback_defaults(self):
        import json

        from google.protobuf.json_format import MessageToDict, ParseDict
        from yandex.cloud.monitoring.v3.dashboard_pb2 import Dashboard

        desired = json.loads(self.control.render(self.control.load_config())["dashboard.json"])
        received = MessageToDict(ParseDict(desired, Dashboard()))
        assert all(received.get(key) == value for key, value in desired.items())
        cfg = self.control.load_config()
        received.update(id=cfg["dashboard_id"], folderId=cfg["folder_id"], etag="3")
        transport = SimpleNamespace(
            dashboard_get=lambda _dashboard_id: received,
            request=lambda *_args: {"absent": True},
        )
        with patch.object(self.control, "preflight"):
            self.assertTrue(self.control.check(cfg, transport)["dashboard_matches"])
            received["widgets"][0]["group"]["title"] = "changed remotely"
            self.assertFalse(self.control.check(cfg, transport)["dashboard_matches"])

    def test_cyrillic_target_name_rejected_before_promtool(self):
        import json
        from subprocess import CompletedProcess

        package = self.control.render(self.control.load_config())
        dashboard = json.loads(package["dashboard.json"])
        dashboard["widgets"][0]["group"]["widgets"][0]["multiSourceChart"]["targets"][0][
            "prometheusTarget"
        ]["name"] = "Свободно"
        package["dashboard.json"] = json.dumps(dashboard)
        with (
            TemporaryDirectory() as directory,
            patch.object(self.control, "render", return_value=package),
            patch.object(
                self.control.subprocess,
                "run",
                return_value=CompletedProcess([], 0, "version 3.5.0", ""),
            ) as run,
        ):
            with self.assertRaisesRegex(self.control.ControlError, "target name"):
                self.control.validate_package(
                    self.control.load_config(), Path(directory), "promtool"
                )
            run.assert_not_called()

    def validate_dashboard(self, package):
        from subprocess import CompletedProcess

        with (
            TemporaryDirectory() as directory,
            patch.object(self.control, "render", return_value=package),
            patch.object(
                self.control.subprocess,
                "run",
                return_value=CompletedProcess([], 0, "version 3.5.0", ""),
            ) as run,
        ):
            self.control.validate_package(self.control.load_config(), Path(directory), "promtool")
            self.assertEqual(
                [call.args[0][1:3] for call in run.call_args_list],
                [
                    ["--version"],
                    ["check", "rules"],
                    ["test", "rules"],
                    ["check", "rules"],
                    ["test", "rules"],
                    ["check", "rules"],
                    ["test", "rules"],
                ],
            )

    def test_invalid_source_and_target_variants_fail_before_promtool(self):
        import json

        # A small SDK-shaped native chart exposes kind/reference validation, independently
        # of whether it is already present in the owned dashboard template.
        chart = {
            "id": "native-health",
            "targets": [
                {
                    "monitoringTarget": {
                        "dataSourceId": "native",
                        "query": '"ua.backlog"{service="custom"}',
                        "name": "A",
                    }
                }
            ],
            "dataSources": [{"monitoringDataSource": {"id": "native"}}],
        }
        prometheus_target = {
            "dataSourceId": "native",
            "workspaceId": "workspace1",
            "query": "up",
            "name": "A",
        }
        cases = {
            "source missing kind": lambda c: c["dataSources"].__setitem__(0, {}),
            "source both kinds": lambda c: c["dataSources"][0].update(
                prometheusDataSource={"id": "native", "step": "60000"}
            ),
            "target missing kind": lambda c: c["targets"].__setitem__(0, {}),
            "target both kinds": lambda c: c["targets"][0].update(
                prometheusTarget=prometheus_target
            ),
            "wrong source reference": lambda c: c["targets"][0]["monitoringTarget"].update(
                dataSourceId="other"
            ),
            "mismatched source kind": lambda c: c["targets"].__setitem__(
                0, {"prometheusTarget": prometheus_target}
            ),
            "empty query": lambda c: c["targets"][0]["monitoringTarget"].update(query=""),
            "unresolved query": lambda c: c["targets"][0]["monitoringTarget"].update(
                query="{{missing}}"
            ),
            "missing workspace": lambda c: c["targets"].__setitem__(
                0, {"prometheusTarget": {**prometheus_target, "workspaceId": ""}}
            ),
            "nonpositive grid": lambda c: c["dataSources"].__setitem__(
                0, {"prometheusDataSource": {"id": "native", "step": "0"}}
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(chart)
                if name in ("missing workspace", "nonpositive grid"):
                    changed["targets"] = [{"prometheusTarget": prometheus_target}]
                    changed["dataSources"] = [
                        {"prometheusDataSource": {"id": "native", "step": "60000"}}
                    ]
                mutate(changed)
                package = {
                    "dashboard.json": json.dumps(
                        {
                            "widgets": [
                                {
                                    "group": {
                                        "id": "group",
                                        "title": "Test",
                                        "widgets": [
                                            {
                                                "id": changed.get("id"),
                                                "position": {"w": "12", "h": "8"},
                                                "multiSourceChart": changed,
                                            }
                                        ],
                                    }
                                }
                            ]
                        }
                    )
                }
                with (
                    TemporaryDirectory() as directory,
                    patch.object(self.control, "render", return_value=package),
                    patch.object(self.control.subprocess, "run") as run,
                ):
                    with self.assertRaises(self.control.ControlError):
                        self.control.validate_package(
                            self.control.load_config(), Path(directory), "promtool"
                        )
                    run.assert_not_called()

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
