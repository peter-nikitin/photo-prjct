from __future__ import annotations

import pytest
from photo_worker.contracts import Claim, ContractError, redact


def processor_configuration() -> dict[str, object]:
    return {
        "retry_policy": {
            "max_attempts": 3,
            "base_backoff_seconds": 30,
            "max_backoff_seconds": 300,
            "jitter_seconds": 5,
            "lease_max_seconds": 300,
        },
        "max_cohort_size": 20,
        "report_max_bytes": 262_144,
        "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
        "capture_metadata": {
            "date_field_precedence": ["DateTimeOriginal", "DateTimeDigitized", "DateTime"],
            "normalization": "utc_assume_utc_if_missing",
        },
        "worker": {
            "concurrency": 1,
            "api_response_max_bytes": 16_384,
            "heartbeat_interval_seconds": 30,
            "lease_duration_seconds": 120,
            "max_input_bytes": 52_428_800,
            "max_pixels": 100_000_000,
            "poll_min_delay_seconds": 5,
            "terminal_result_max_bytes": 8_192,
        },
    }


def claim_payload(**overrides: object) -> dict[str, object]:
    job: dict[str, object] = {
        "id": "00000000-0000-0000-0000-000000000011",
        "attempt_id": "00000000-0000-0000-0000-000000000012",
        "contract_version": 1,
        "processor_type": "capture_metadata",
        "processor_version": 1,
        "configuration": processor_configuration(),
        "photo_id": "photo-1",
        "event_id": "00000000-0000-0000-0000-000000000013",
        "run_id": "00000000-0000-0000-0000-000000000014",
        "input_fingerprint": {
            "original_key": "originals/0123456789abcdef0123456789abcdef",
            "original_size": 1024,
            "original_content_type": "image/jpeg",
            "verified_source_etag": "etag-1",
            "version_evidence": "verified_source_etag",
        },
        "input_limits": {"max_bytes": 1024, "content_type": "image/jpeg"},
        # Django's JsonResponse serializes aware UTC values via ``datetime.isoformat()``.
        "lease_expires_at": "2026-07-29T10:03:00+00:00",
        "download_url": "https://storage.example.test/original?X-Amz-Signature=secret",
        "download_expires_at": "2026-07-29T10:01:00+00:00",
    }
    return {"empty": False, "job": job | overrides}


def test_claim_accepts_only_the_supported_processor_contract() -> None:
    claim = Claim.from_response(claim_payload())

    assert claim.job is not None
    assert claim.job.processor_type == "capture_metadata"
    assert claim.job.input_limits.max_bytes == 1024
    assert claim.job.input_fingerprint.verified_source_etag == "etag-1"
    assert claim.job.configuration.max_pixels == 100_000_000
    assert claim.job.configuration.lease_duration_seconds == 120
    assert claim.job.run_id == "00000000-0000-0000-0000-000000000014"


@pytest.mark.parametrize(
    "overrides",
    [
        {"contract_version": 2},
        {"processor_type": "faces"},
        {"processor_version": 2},
        {"input_limits": {"max_bytes": 0, "content_type": "image/jpeg"}},
        {"input_limits": {"max_bytes": 10, "content_type": "image/jpeg"}},
        {"input_fingerprint": {"verified_source_etag": "etag-1"}},
        {"configuration": {}},
        {"unexpected": "value"},
    ],
)
def test_claim_rejects_incompatible_or_unbounded_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ContractError):
        Claim.from_response(claim_payload(**overrides))


def test_claim_rejects_hostile_wire_identifiers() -> None:
    with pytest.raises(ContractError):
        Claim.from_response(claim_payload(photo_id="https://storage.example.test/x?token=secret"))
    with pytest.raises(ContractError):
        Claim.from_response(claim_payload(run_id="not-a-uuid"))


def test_redaction_removes_urls_and_bearer_secrets_from_log_values() -> None:
    value = "GET https://storage.example.test/x?X-Amz-Signature=secret token worker-secret"

    redacted = redact(value, secrets=("worker-secret",))

    assert "storage.example" not in redacted
    assert "worker-secret" not in redacted
    assert "<redacted>" in redacted
