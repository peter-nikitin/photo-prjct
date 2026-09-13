from __future__ import annotations

import pytest
from photo_worker.contracts import BIB_INFERENCE_CONFIGURATION_SHA256, Claim, ContractError
from photo_worker.runner import WorkerConfig


def configuration() -> dict[str, object]:
    return {
        "retry_policy": {
            "max_attempts": 3,
            "base_backoff_seconds": 30,
            "max_backoff_seconds": 300,
            "jitter_seconds": 5,
            "lease_max_seconds": 300,
        },
        "max_cohort_size": 100,
        "report_max_bytes": 262_144,
        "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
        "bib_recognition": {
            "inference_configuration_sha256": BIB_INFERENCE_CONFIGURATION_SHA256,
            "generation": 1,
            "deadline_seconds": 300,
            "result_max_bytes": 120 * 1024,
        },
        "worker": {
            "api_response_max_bytes": 128 * 1024,
            "concurrency": 1,
            "heartbeat_interval_seconds": 30,
            "lease_duration_seconds": 120,
            "max_input_bytes": 50 * 1024 * 1024,
            "max_pixels": 100_000_000,
            "poll_min_delay_seconds": 5,
            "terminal_result_max_bytes": 128 * 1024,
        },
    }


def claim_payload() -> dict[str, object]:
    return {
        "empty": False,
        "job": {
            "id": "00000000-0000-0000-0000-000000000011",
            "attempt_id": "00000000-0000-0000-0000-000000000012",
            "contract_version": 1,
            "processor_type": "bib_recognition",
            "processor_version": 1,
            "configuration": configuration(),
            "photo_id": "photo-1",
            "event_id": "1",
            "run_id": "00000000-0000-0000-0000-000000000014",
            "input_fingerprint": {
                "original_key": "originals/0123456789abcdef0123456789abcdef",
                "original_size": 1024,
                "original_content_type": "image/jpeg",
                "verified_source_etag": "etag",
                "version_evidence": "verified_source_etag",
                "source_sha256": "c" * 64,
            },
            "input_limits": {"max_bytes": 1024, "content_type": "image/jpeg"},
            "lease_expires_at": "2026-09-07T12:00:00+00:00",
            "download_url": "https://storage.yandexcloud.net/bucket/key?X-Amz-Signature=secret",
            "download_expires_at": "2026-09-07T11:59:00+00:00",
        },
    }


def test_claim_accepts_only_exact_bib_identity_configuration_and_source_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOTO_WORKER_ALLOW_INSECURE_LOCAL_MINIO", "true")
    claim = Claim.from_response(claim_payload())
    assert claim.job is not None
    assert claim.job.input_fingerprint.source_sha256 == "c" * 64
    altered = claim_payload()
    job = altered["job"]
    assert isinstance(job, dict)
    config = job["configuration"]
    assert isinstance(config, dict)
    bib = config["bib_recognition"]
    assert isinstance(bib, dict)
    bib["deadline_seconds"] = 299
    with pytest.raises(ContractError, match="configuration"):
        Claim.from_response(altered)


def test_bib_identity_uses_bundled_runtime_without_loading_models():
    config = WorkerConfig(
        worker_build="bib", lease_seconds=120, processor_identities=("1/bib_recognition/1",)
    )
    assert config.concurrency == 1


def test_claim_hash_matches_computed_linux_configuration():
    from photo_worker.bib_recognition import BIB_CONFIGURATION_SHA256

    assert BIB_INFERENCE_CONFIGURATION_SHA256 == BIB_CONFIGURATION_SHA256


def test_bib_original_claim_without_stored_sha_remains_bound_to_transport():
    payload = claim_payload()
    fingerprint = payload["job"]["input_fingerprint"]
    del fingerprint["source_sha256"]
    job = Claim.from_response(payload).job
    assert job is not None
    assert job.input_fingerprint.verified_source_etag == "etag"
    assert job.input_fingerprint.original_size == 1024
    assert job.input_fingerprint.source_sha256 is None


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [("worker", "max_pixels", 10_000_000), ("bib_recognition", "generation", True)],
)
def test_bib_claim_rejects_changed_generation_configuration(section, key, value):
    payload = claim_payload()
    payload["job"]["configuration"][section][key] = value
    with pytest.raises(ContractError):
        Claim.from_response(payload)


def test_bib_claim_rejects_a_non_original_storage_key():
    payload = claim_payload()
    payload["job"]["input_fingerprint"]["original_key"] = "other/key"
    with pytest.raises(ContractError):
        Claim.from_response(payload)
