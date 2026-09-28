from photo_worker.bib_recognition import BIB_CONFIGURATION_SHA256
from photo_worker.contracts import (
    BIB_INFERENCE_CONFIGURATION_SHA256 as WORKER_BIB_INFERENCE_CONFIGURATION_SHA256,
)
from processing.services.bibs import BIB_INFERENCE_CONFIGURATION_SHA256, bib_configuration

LINUX_BIB_CONFIGURATION_SHA256 = "32b3f2c94202df7c90e5c799c9ca21b760d5d2ac9c376fe578f6f330e415edf9"


def test_backend_claim_uses_the_packaged_linux_bib_configuration_identity() -> None:
    bib = bib_configuration()["bib_recognition"]
    assert isinstance(bib, dict)
    assert BIB_INFERENCE_CONFIGURATION_SHA256 == LINUX_BIB_CONFIGURATION_SHA256
    assert bib["inference_configuration_sha256"] == LINUX_BIB_CONFIGURATION_SHA256
    assert WORKER_BIB_INFERENCE_CONFIGURATION_SHA256 == LINUX_BIB_CONFIGURATION_SHA256
    assert BIB_CONFIGURATION_SHA256 == LINUX_BIB_CONFIGURATION_SHA256


def test_compact_maximum_runtime_envelope_fits_ingress_cap():
    """All six processor kinds/outcomes remain bounded even at maximum scalar precision."""
    import json

    from processing.services.worker_pool_telemetry import FIELDS

    aggregates = {
        kind: {
            outcome: [2**53, 9007199254740991.0, [2**53] * 8]
            for outcome in (
                "callback_delivered",
                "execution_failed",
                "transport_failed",
                "lease_lost",
            )
        }
        for kind in (
            "capture_metadata",
            "generate_preview",
            "generate_watermarked_preview",
            "face_embedding",
            "bib_recognition",
            "selfie_query",
        )
    }
    payload = dict.fromkeys(FIELDS)
    payload.update(
        pool="bulk",
        instance_id="a" * 64,
        boot_id="12345678-1234-1234-1234-123456789012",
        worker_build="a" * 40,
        zone_id="ru-central1-a",
        collector_epoch="12345678-1234-1234-1234-123456789013",
        collector_started_at="2026-09-28T12:00:00.000001+00:00",
        sequence=2**53,
        sampled_at="2026-09-28T12:00:30.000001+00:00",
        host={
            "cpu_utilization": 0.9999999999999999,
            "memory_available_bytes": 2**53,
            "memory_total_bytes": 2**53,
            "root_available_bytes": 2**53,
            "root_total_bytes": 2**53,
        },
        container={
            "present": True,
            "running": True,
            "container_id": "c" * 64,
            "restart_count": 2**53,
            "oom_killed": False,
            "exit_code": 2**53,
            "cpu_usage_cores": 9007199254740991.0,
            "cpu_limit_cores": 9007199254740991.0,
            "memory_usage_bytes": 2**53,
            "memory_limit_bytes": 2**53,
            "events_available": True,
            "events_since": "2026-09-28T12:00:00.000001+00:00",
            "oom_events": 2**53,
            "restart_events": 2**53,
        },
        runtime={
            "registration_generation": "12345678-1234-1234-1234-123456789014",
            "sampled_at": "2026-09-28T12:00:30.000001+00:00",
            "busy": 1,
            "aggregates": aggregates,
        },
    )
    assert len(json.dumps(payload, separators=(",", ":")).encode()) < 16384
