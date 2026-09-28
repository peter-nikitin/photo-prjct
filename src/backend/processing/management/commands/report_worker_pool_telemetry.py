"""Read-only diagnostic aggregates; no delivery or processing control operation."""

import json

from django.core.management.base import BaseCommand
from django.utils import timezone
from prometheus_client.parser import text_string_to_metric_families

from processing.services.worker_pool_telemetry import generate_diagnostic_metrics


class Command(BaseCommand):
    help = "Report bounded worker diagnostic freshness counts; cloud delivery remains unverified."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--json", action="store_true", required=True)

    def handle(self, *args, **options) -> None:
        pools = {
            pool: {
                "expected": 0,
                "cloud_fresh": 0,
                "missing": 0,
                "host_fresh": 0,
                "runtime_fresh": 0,
                "scalar_samples": 0,
            }
            for pool in ("bulk", "selfie")
        }
        counts = {
            "worker_cloud_observation_fresh": "cloud_fresh",
            "worker_host_observation_missing": "missing",
            "worker_host_observation_fresh": "host_fresh",
            "worker_runtime_observation_fresh": "runtime_fresh",
        }
        for family in text_string_to_metric_families(generate_diagnostic_metrics().decode()):
            for sample in family.samples:
                pool = pools[sample.labels["pool"]]
                pool["scalar_samples"] += 1
                if sample.name in counts:
                    pool[counts[sample.name]] += int(sample.value)
                if sample.name == "worker_cloud_observation_fresh":
                    pool["expected"] += 1
        self.stdout.write(
            json.dumps(
                {
                    "observed_at": timezone.now().isoformat(),
                    "pools": pools,
                    "remote_write": "unverified",
                    "alerts": "deferred",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
