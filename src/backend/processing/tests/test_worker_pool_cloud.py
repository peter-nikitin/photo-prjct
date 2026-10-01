from copy import deepcopy
from typing import Any
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from processing.models import WorkerPool
from processing.services.worker_pool_cloud import CloudReader
from processing.services.worker_pool_lifecycle import configure_pool


@override_settings(PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True)
class CloudObservationTests(TestCase):
    def setUp(self):
        configure_pool("bulk", group_id="bulk-group", active_build="a" * 40)
        self.config: dict[str, Any] = {
            "folder_id": "folder",
            "canonical_folder_id": "canonical-folder",
            "zone": "ru-central1-a",
            "groups": {"bulk": "bulk-group"},
            "boot_image_id": "boot-image",
            "releases": {
                "a" * 40: "ghcr.io/example/photo-prjct-worker@sha256:" + "a" * 64,
                "b" * 40: "ghcr.io/example/photo-prjct-worker@sha256:" + "b" * 64,
            },
        }
        self.group: dict[str, Any] = {
            "id": "bulk-group",
            "folderId": "folder",
            "allocationPolicy": {"zones": [{"zoneId": "ru-central1-a"}]},
            "instanceTemplate": {
                "metadata": {
                    "findme-worker-build": "b" * 40,
                    "findme-worker-image": self.config["releases"]["b" * 40],
                },
                "bootDiskSpec": {"diskSpec": {"imageId": "boot-image"}},
            },
            "managedInstancesState": {"targetSize": "2"},
        }
        self.instance = {
            "id": "old-node",
            "folderId": "folder",
            "zoneId": "ru-central1-a",
            "metadata": {
                "findme-worker-build": "a" * 40,
                "findme-worker-image": self.config["releases"]["a" * 40],
            },
            "bootDisk": {"diskId": "disk"},
            "networkInterfaces": [{"primaryV4Address": {"address": "10.0.0.4"}}],
        }

    def get(self, path, **parameters):
        if path == "instanceGroups/bulk-group":
            return deepcopy(self.group)
        if path == "instanceGroups/bulk-group/instances":
            if not parameters["pageToken"]:
                return {
                    "instances": [
                        {
                            "instanceId": "old-node",
                            "zoneId": "ru-central1-a",
                            "status": "RUNNING_OUTDATED",
                        }
                    ],
                    "nextPageToken": "next",
                }
            return {
                "instances": [
                    {"instanceId": "", "zoneId": "ru-central1-a", "status": "CREATING_INSTANCE"}
                ]
            }
        if path == "instances/old-node":
            return deepcopy(self.instance)
        if path == "disks/disk":
            return {"id": "disk", "folderId": "folder", "sourceImageId": "boot-image"}
        raise AssertionError(path)

    def test_complete_pages_preserve_actual_old_build_and_known_pending_slot(self):
        from processing.services.worker_pool_observation import observe_cloud

        reader = CloudReader("fake-token")
        with patch.object(reader, "get", side_effect=self.get):
            self.assertTrue(observe_cloud("bulk", self.config, reader=reader))
        pool = WorkerPool.objects.get(name="bulk")
        self.assertEqual(
            pool.observed_members,
            [
                {"instance_id": "old-node", "status": "RUNNING_OUTDATED", "worker_build": "a" * 40},
                {"instance_id": "", "status": "CREATING_INSTANCE", "worker_build": ""},
            ],
        )

    def test_omitted_zero_target_records_actual_running_member(self):
        from processing.services.worker_pool_observation import observe_cloud

        self.group["managedInstancesState"] = {"runningActualCount": "1"}
        self.group["instanceTemplate"]["metadata"] = self.instance["metadata"]

        def zero_target(path, **parameters):
            if path == "instanceGroups/bulk-group/instances":
                return {
                    "instances": [
                        {
                            "instanceId": "old-node",
                            "zoneId": "ru-central1-a",
                            "status": "RUNNING_ACTUAL",
                        }
                    ]
                }
            return self.get(path, **parameters)

        reader = CloudReader("fake-token")
        with patch.object(reader, "get", side_effect=zero_target):
            self.assertTrue(observe_cloud("bulk", self.config, reader=reader))
        pool = WorkerPool.objects.get(name="bulk")
        self.assertEqual(pool.target_size, 0)
        self.assertEqual(
            pool.observed_members,
            [
                {
                    "instance_id": "old-node",
                    "status": "RUNNING_ACTUAL",
                    "worker_build": "a" * 40,
                }
            ],
        )

    def test_missing_or_malformed_managed_state_never_records_implicit_zero(self):
        from processing.services.worker_pool_observation import observe_cloud

        reader = CloudReader("fake-token")
        invalid_states: tuple[Any, ...] = (None, [], "0")
        for managed in invalid_states:
            self.group["managedInstancesState"] = managed
            with (
                self.subTest(managed=managed),
                patch.object(reader, "get", side_effect=self.get),
                self.assertRaises((ValueError, TypeError)),
            ):
                observe_cloud("bulk", self.config, reader=reader)
            self.assertEqual(WorkerPool.objects.get(name="bulk").observation_sequence, 0)
        del self.group["managedInstancesState"]
        with patch.object(reader, "get", side_effect=self.get), self.assertRaises(KeyError):
            observe_cloud("bulk", self.config, reader=reader)
        self.assertEqual(WorkerPool.objects.get(name="bulk").observation_sequence, 0)

    def test_worker_instance_and_disk_must_belong_to_worker_folder(self):
        from processing.services.worker_pool_observation import observe_cloud

        reader = CloudReader("fake-token")
        self.instance["folderId"] = "canonical-folder"
        with patch.object(reader, "get", side_effect=self.get), self.assertRaises(ValueError):
            observe_cloud("bulk", self.config, reader=reader)
        self.instance["folderId"] = "folder"

        def wrong_disk(path, **parameters):
            if path == "disks/disk":
                return {"id": "disk", "folderId": "canonical-folder", "sourceImageId": "boot-image"}
            return self.get(path, **parameters)

        with patch.object(reader, "get", side_effect=wrong_disk), self.assertRaises(ValueError):
            observe_cloud("bulk", self.config, reader=reader)
        self.assertEqual(WorkerPool.objects.get(name="bulk").observation_sequence, 0)

    def test_worker_observation_requires_distinct_folder_inputs_before_cloud_reads(self):
        from processing.services.worker_pool_observation import observe_cloud

        reader = Mock()
        for change in ({"canonical_folder_id": None}, {"canonical_folder_id": "folder"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                observe_cloud("bulk", self.config | change, reader=reader)
        missing = deepcopy(self.config)
        del missing["canonical_folder_id"]
        with self.assertRaises(ValueError):
            observe_cloud("bulk", missing, reader=reader)
        reader.get.assert_not_called()

    def test_partial_pagination_never_replaces_previous_complete_evidence(self):
        from processing.services.worker_pool_observation import observe_cloud

        reader = CloudReader("fake-token")
        with patch.object(reader, "get", side_effect=self.get):
            observe_cloud("bulk", self.config, reader=reader)
        before = WorkerPool.objects.values().get(name="bulk")

        def failed_page(path, **parameters):
            if parameters.get("pageToken") == "next":
                raise ValueError("partial")
            return self.get(path, **parameters)

        with patch.object(reader, "get", side_effect=failed_page), self.assertRaises(ValueError):
            observe_cloud("bulk", self.config, reader=reader)
        self.assertEqual(WorkerPool.objects.values().get(name="bulk"), before)

    def test_missing_or_wrong_actual_image_evidence_cannot_be_relabelled_from_template(self):
        from processing.services.worker_pool_observation import observe_cloud

        reader = CloudReader("fake-token")
        for metadata in (
            {},
            {
                "findme-worker-build": "a" * 40,
                "findme-worker-image": self.config["releases"]["b" * 40],
            },
        ):
            self.instance["metadata"] = metadata
            with patch.object(reader, "get", side_effect=self.get), self.assertRaises(ValueError):
                observe_cloud("bulk", self.config, reader=reader)
        self.assertEqual(WorkerPool.objects.get(name="bulk").observation_sequence, 0)

    def test_out_of_order_snapshot_is_rejected_by_coordinator(self):
        from processing.services.worker_pool_observation import observe_cloud

        reader = CloudReader("fake-token")
        with patch.object(reader, "get", side_effect=self.get):
            self.assertTrue(observe_cloud("bulk", self.config, reader=reader))
            with patch(
                "processing.services.worker_pool_observation.timezone.now",
                return_value=timezone.now() - timezone.timedelta(minutes=1),
            ):
                self.assertFalse(observe_cloud("bulk", self.config, reader=reader))

    def test_noncanonical_registry_or_malformed_digest_cannot_begin_cloud_observation(self):
        from processing.services.worker_pool_observation import observe_cloud

        reader = Mock()
        for image in (
            "cr.yandex/registry/worker@sha256:" + "a" * 64,
            "ghcr.io/example/photo-prjct-worker@sha256:wrong",
        ):
            config = deepcopy(self.config)
            config["releases"]["a" * 40] = image
            with self.assertRaises(ValueError):
                observe_cloud("bulk", config, reader=reader)
        reader.get.assert_not_called()

    def test_snapshot_collection_longer_than_coordinator_freshness_is_rejected(self):
        from processing.services.worker_pool_observation import observe_cloud

        reader = CloudReader("fake-token")
        now = timezone.now()
        with (
            patch.object(reader, "get", side_effect=self.get),
            patch(
                "processing.services.worker_pool_observation.timezone.now",
                side_effect=[now, now + timezone.timedelta(seconds=91)],
            ),
            self.assertRaises(ValueError),
        ):
            observe_cloud("bulk", self.config, reader=reader)
        self.assertEqual(WorkerPool.objects.get(name="bulk").observation_sequence, 0)


class CloudTransportTests(SimpleTestCase):
    def test_writer_accepts_only_complete_documented_monitoring_success(self):
        from processing.services.worker_pool_cloud import write_metrics

        metrics = [
            {
                "name": "worker_pool_workload",
                "type": "DGAUGE",
                "value": 0,
                "labels": {"pool": "bulk", "zone_id": "ru-central1-a"},
            }
        ]
        with patch(
            "processing.services.worker_pool_cloud.metadata_token", return_value="short-token"
        ):
            for payload in (
                {"writtenMetricsCount": "1"},
                {"writtenMetricsCount": "0"},
                {"writtenMetricsCount": "1", "errorMessage": "private-detail"},
                {"metrics_written": 1},
            ):
                with patch(
                    "processing.services.worker_pool_cloud.request_json", return_value=payload
                ):
                    if payload == {"writtenMetricsCount": "1"}:
                        write_metrics("folder", metrics)
                    else:
                        with self.assertRaises(ValueError):
                            write_metrics("folder", metrics)

    def test_writer_accepts_observed_numeric_monitoring_success(self):
        from processing.services.worker_pool_cloud import write_metrics

        metrics = [
            {
                "name": "worker_pool_workload",
                "type": "DGAUGE",
                "value": 0,
                "labels": {"pool": "bulk", "zone_id": "ru-central1-a"},
            }
        ]
        with (
            patch(
                "processing.services.worker_pool_cloud.metadata_token", return_value="short-token"
            ),
            patch(
                "processing.services.worker_pool_cloud.request_json",
                return_value={"writtenMetricsCount": 1},
            ),
        ):
            write_metrics("folder", metrics)

    def test_transport_rejects_oversized_or_non_success_response(self):
        from urllib.request import Request

        from processing.services.worker_pool_cloud import request_json

        response = Mock(status=200)
        response.read.return_value = b"x" * 1_048_577
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch("processing.services.worker_pool_cloud.build_opener", return_value=opener):
            with self.assertRaises(ValueError):
                request_json(
                    Request("https://compute.api.cloud.yandex.net/compute/v1/instanceGroups/group")
                )
            response.status = 302
            with self.assertRaises(ValueError):
                request_json(
                    Request("https://compute.api.cloud.yandex.net/compute/v1/instanceGroups/group")
                )

    def test_metadata_identity_rejects_long_lived_missing_expiry_or_injected_token(self):
        from processing.services.worker_pool_cloud import metadata_token

        for payload in (
            {"access_token": "token"},
            {"access_token": "token", "expires_in": 999999},
            {"access_token": "private\nvalue", "expires_in": 60},
        ):
            with (
                patch("processing.services.worker_pool_cloud.request_json", return_value=payload),
                self.assertRaises(ValueError),
            ):
                metadata_token()
