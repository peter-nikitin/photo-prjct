from __future__ import annotations

import pytest
from photo_worker.contracts import (
    PROCESSOR_TYPE,
    PROCESSOR_TYPE_FACE_EMBEDDING,
    PROCESSOR_TYPE_SELFIE_QUERY,
    V2_FACE_EMBEDDING_CONFIGURATION,
    V2_GENERATE_PREVIEW_CONFIGURATION,
    Claim,
    ContractError,
    redact,
)


def test_preview_contract_caps_current_multibuffer_pipeline_at_24_megapixels() -> None:
    worker = V2_GENERATE_PREVIEW_CONFIGURATION["worker"]

    assert isinstance(worker, dict)
    assert worker["max_pixels"] == 24_000_000


def test_v2_face_contract_reserves_128_kib_for_a_maximum_typed_terminal_payload() -> None:
    worker = V2_FACE_EMBEDDING_CONFIGURATION["worker"]

    assert isinstance(worker, dict)
    assert worker["terminal_result_max_bytes"] == 128 * 1024
    assert worker["api_response_max_bytes"] == 128 * 1024


def preview_configuration() -> dict[str, object]:
    return {
        "retry_policy": {
            "max_attempts": 3,
            "base_backoff_seconds": 30,
            "max_backoff_seconds": 300,
            "jitter_seconds": 5,
            "lease_max_seconds": 300,
        },
        "max_cohort_size": 16,
        "report_max_bytes": 262_144,
        "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
        "generate_preview": {
            "variant": "preview-small-v1",
            "output_format": "jpeg",
            "max_long_edge": 1600,
            "jpeg_quality": 85,
            "color_space": "srgb",
            "upscale": False,
            "apply_exif_orientation": True,
            "strip_metadata": True,
            "watermark": "none",
            "max_output_bytes": 10_485_760,
            "max_output_width": 1600,
            "max_output_height": 1600,
            "checksum_algorithm": "sha256",
        },
        "worker": {
            "concurrency": 1,
            "api_response_max_bytes": 16_384,
            "heartbeat_interval_seconds": 30,
            "lease_duration_seconds": 120,
            "max_input_bytes": 52_428_800,
            "max_pixels": 24_000_000,
            "poll_min_delay_seconds": 5,
            "terminal_result_max_bytes": 8_192,
        },
    }


def preview_face_configuration() -> dict[str, object]:
    return {
        "retry_policy": {
            "max_attempts": 3,
            "base_backoff_seconds": 30,
            "max_backoff_seconds": 300,
            "jitter_seconds": 5,
            "lease_max_seconds": 300,
        },
        "max_cohort_size": 16,
        "report_max_bytes": 262_144,
        "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
        "face_embedding": {
            "model": "sface",
            "min_face_px": 32,
            "max_faces_per_photo": 32,
            "normalize_embeddings": True,
        },
        "worker": {
            "concurrency": 1,
            "api_response_max_bytes": 128 * 1024,
            "heartbeat_interval_seconds": 30,
            "lease_duration_seconds": 120,
            "max_input_bytes": 52_428_800,
            "max_pixels": 100_000_000,
            "poll_min_delay_seconds": 5,
            "terminal_result_max_bytes": 128 * 1024,
        },
    }


def preview_claim_payload(**overrides: object) -> dict[str, object]:
    return {
        "empty": False,
        "job": {
            "id": "00000000-0000-0000-0000-000000000011",
            "attempt_id": "00000000-0000-0000-0000-000000000012",
            "contract_version": 2,
            "processor_type": "generate_preview",
            "processor_version": 1,
            "configuration": preview_configuration(),
            "photo_id": "photo-1",
            "event_id": "00000000-0000-0000-0000-000000000013",
            "run_id": "00000000-0000-0000-0000-000000000014",
            "input_fingerprint": {
                "object_key": "originals/0123456789abcdef0123456789abcdef",
                "object_size": 1024,
                "object_content_type": "image/jpeg",
                "object_etag": "etag-1",
                "media_kind": "original",
                "pixel_width": 3200,
                "pixel_height": 2000,
            },
            "input_limits": {"max_bytes": 1024, "content_type": "image/jpeg"},
            "lease_expires_at": "2026-07-30T10:00:00Z",
            "download_url": "https://storage.example.test/download?signature=secret",
            "download_expires_at": "2026-07-30T10:00:00Z",
            "output_slots": [
                {
                    "variant": "preview-small-v1",
                    "upload_url": "https://storage.example.test/upload?signature=secret",
                    "upload_expires_at": "2026-07-30T10:00:00Z",
                    "content_type": "image/jpeg",
                    "staging_key": (
                        "processing-staging/previews/00000000-0000-0000-0000-000000000012/"
                        "preview-small-v1.jpg"
                    ),
                    "max_bytes": 10_485_760,
                    "max_width": 1600,
                    "max_height": 1600,
                    "checksum_algorithm": "sha256",
                }
            ],
        }
        | overrides,
    }


def test_claim_accepts_v2_preview_with_exact_generic_input_and_upload_slot() -> None:
    claim = Claim.from_response(preview_claim_payload())

    assert claim.job is not None
    assert claim.job.contract_version == 2
    assert claim.job.processor_type == "generate_preview"
    assert claim.job.input_fingerprint.media_kind == "original"
    assert claim.job.output_slots[0].variant == "preview-small-v1"


def test_claim_accepts_v2_face_embedding_only_with_generic_preview_input() -> None:
    payload = preview_claim_payload()
    job = payload["job"]
    assert isinstance(job, dict)
    job.update(
        {
            "processor_type": "face_embedding",
            "processor_version": 2,
            "configuration": preview_face_configuration(),
            "input_fingerprint": {
                "object_key": "derivatives/previews/photo-1/preview-small-v1/"
                "00000000-0000-0000-0000-000000000012-"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
                "object_size": 1024,
                "object_content_type": "image/jpeg",
                "object_etag": None,
                "media_kind": "preview-small-v1",
                "pixel_width": 1600,
                "pixel_height": 1000,
            },
            "input_geometry": {
                "coordinate_space": "preview-small-v1",
                "pixel_width": 1600,
                "pixel_height": 1000,
                "oriented_source_width": 3200,
                "oriented_source_height": 2000,
            },
        }
    )
    job.pop("output_slots")

    claim = Claim.from_response(payload)

    assert claim.job is not None
    assert claim.job.contract_version == 2
    assert claim.job.processor_version == 2
    assert claim.job.input_fingerprint.media_kind == "preview-small-v1"


def test_v2_face_claim_rejects_a_transport_bound_that_is_neither_legacy_nor_current() -> None:
    payload = preview_claim_payload()
    job = payload["job"]
    assert isinstance(job, dict)
    configuration = preview_face_configuration()
    worker = configuration["worker"]
    assert isinstance(worker, dict)
    worker["terminal_result_max_bytes"] = 8_193
    job.update(
        {
            "processor_type": "face_embedding",
            "processor_version": 2,
            "configuration": configuration,
            "input_fingerprint": {
                "object_key": "derivatives/previews/photo-1/preview-small-v1/"
                "00000000-0000-0000-0000-000000000012-"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
                "object_size": 1024,
                "object_content_type": "image/jpeg",
                "object_etag": None,
                "media_kind": "preview-small-v1",
                "pixel_width": 1600,
                "pixel_height": 1000,
            },
            "input_geometry": {
                "coordinate_space": "preview-small-v1",
                "pixel_width": 1600,
                "pixel_height": 1000,
                "oriented_source_width": 3200,
                "oriented_source_height": 2000,
            },
        }
    )
    job.pop("output_slots")

    with pytest.raises(ContractError):
        Claim.from_response(payload)


def test_v2_face_claim_rejects_the_superseded_8_kib_transport_snapshot() -> None:
    payload = preview_claim_payload()
    job = payload["job"]
    assert isinstance(job, dict)
    configuration = preview_face_configuration()
    worker = configuration["worker"]
    assert isinstance(worker, dict)
    worker["api_response_max_bytes"] = 16_384
    worker["terminal_result_max_bytes"] = 8_192
    job.update(
        {
            "processor_type": "face_embedding",
            "processor_version": 2,
            "configuration": configuration,
            "input_fingerprint": {
                "object_key": "derivatives/previews/photo-1/preview-small-v1/"
                "00000000-0000-0000-0000-000000000012-"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
                "object_size": 1024,
                "object_content_type": "image/jpeg",
                "object_etag": None,
                "media_kind": "preview-small-v1",
                "pixel_width": 1600,
                "pixel_height": 1000,
            },
            "input_geometry": {
                "coordinate_space": "preview-small-v1",
                "pixel_width": 1600,
                "pixel_height": 1000,
                "oriented_source_width": 3200,
                "oriented_source_height": 2000,
            },
        }
    )
    job.pop("output_slots")

    with pytest.raises(ContractError):
        Claim.from_response(payload)


@pytest.mark.parametrize(
    "processor_type,processor_version,configuration_kind,media_kind,has_output_slot",
    [
        ("generate_preview", 1, "face", "original", True),
        ("generate_preview", 1, "preview", "preview-small-v1", True),
        ("generate_preview", 1, "preview", "original", False),
        ("face_embedding", 2, "preview", "preview-small-v1", False),
        ("face_embedding", 2, "face", "original", False),
        ("face_embedding", 2, "face", "preview-small-v1", True),
    ],
)
def test_v2_claim_rejects_swapped_configuration_media_kind_or_output_slot(
    processor_type: str,
    processor_version: int,
    configuration_kind: str,
    media_kind: str,
    has_output_slot: bool,
) -> None:
    payload = preview_claim_payload()
    job = payload["job"]
    assert isinstance(job, dict)
    fingerprint = job["input_fingerprint"]
    assert isinstance(fingerprint, dict)
    job.update(
        {
            "processor_type": processor_type,
            "processor_version": processor_version,
            "configuration": (
                preview_configuration()
                if configuration_kind == "preview"
                else processor_configuration("face_embedding")
            ),
        }
    )
    fingerprint["media_kind"] = media_kind
    if not has_output_slot:
        job.pop("output_slots")

    with pytest.raises(ContractError):
        Claim.from_response(payload)


@pytest.mark.parametrize(
    "field,value",
    [("max_cohort_size", 17), ("report_max_bytes", 262_143)],
)
def test_v2_preview_claim_rejects_immutable_configuration_mutation(field: str, value: int) -> None:
    payload = preview_claim_payload()
    job = payload["job"]
    assert isinstance(job, dict)
    configuration = job["configuration"]
    assert isinstance(configuration, dict)
    configuration[field] = value

    with pytest.raises(ContractError):
        Claim.from_response(payload)


def processor_configuration(processor_type: str = PROCESSOR_TYPE) -> dict[str, object]:
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
        **(
            {
                "capture_metadata": {
                    "date_field_precedence": [
                        "DateTimeOriginal",
                        "DateTimeDigitized",
                        "DateTime",
                    ],
                    "normalization": "utc_assume_utc_if_missing",
                }
            }
            if processor_type == PROCESSOR_TYPE
            else {
                "face_embedding": {
                    "max_faces": 2,
                    "detection_threshold": 0.75,
                }
            }
        ),
    }


def claim_payload(
    *,
    processor_type: str = PROCESSOR_TYPE,
    configuration: dict[str, object] | None = None,
    **overrides: object,
) -> dict[str, object]:
    if configuration is None:
        configuration = processor_configuration(processor_type)

    job: dict[str, object] = {
        "id": "00000000-0000-0000-0000-000000000011",
        "attempt_id": "00000000-0000-0000-0000-000000000012",
        "contract_version": 1,
        "processor_type": processor_type,
        "processor_version": 1,
        "configuration": configuration,
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
    assert claim.job.processor_type == PROCESSOR_TYPE
    assert claim.job.input_limits.max_bytes == 1024
    assert claim.job.input_fingerprint.verified_source_etag == "etag-1"
    assert claim.job.configuration.max_pixels == 100_000_000
    assert claim.job.configuration.lease_duration_seconds == 120
    assert claim.job.run_id == "00000000-0000-0000-0000-000000000014"


def test_claim_accepts_face_embedding_processor_contract() -> None:
    claim = Claim.from_response(
        claim_payload(
            processor_type=PROCESSOR_TYPE_FACE_EMBEDDING,
            configuration={
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
                "face_embedding": {
                    "max_faces": 3,
                    "detection_threshold": 0.8,
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
            },
        )
    )

    assert claim.job is not None
    assert claim.job.processor_type == PROCESSOR_TYPE_FACE_EMBEDDING
    assert claim.job.configuration.max_faces == 3
    assert claim.job.configuration.face_detection_threshold == 0.8


def test_claim_accepts_face_embedding_configuration_with_legacy_backend_fields() -> None:
    claim = Claim.from_response(
        claim_payload(
            processor_type=PROCESSOR_TYPE_FACE_EMBEDDING,
            configuration={
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
                "face_embedding": {
                    "model": "sface",
                    "max_faces_per_photo": 32,
                    "min_face_px": 32,
                    "normalize_embeddings": True,
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
            },
        )
    )

    assert claim.job is not None
    assert claim.job.processor_type == PROCESSOR_TYPE_FACE_EMBEDDING
    assert claim.job.configuration.max_faces == 32
    assert claim.job.configuration.model == "sface"


@pytest.mark.parametrize(
    "overrides",
    [
        {"contract_version": 2},
        {"processor_type": "faces"},
        {"processor_version": 2},
        {
            "processor_type": PROCESSOR_TYPE_FACE_EMBEDDING,
            "processor_version": 2,
            "configuration": processor_configuration(processor_type=PROCESSOR_TYPE_FACE_EMBEDDING),
        },
        {"input_limits": {"max_bytes": 0, "content_type": "image/jpeg"}},
        {"input_limits": {"max_bytes": 10, "content_type": "image/jpeg"}},
        {"input_fingerprint": {"verified_source_etag": "etag-1"}},
        {"configuration": {}},
        {"unexpected": "value"},
        {
            "configuration": {
                **processor_configuration(processor_type=PROCESSOR_TYPE_FACE_EMBEDDING),
                "face_embedding": {"max_faces": 0},
            }
        },
        {
            "configuration": {
                **processor_configuration(processor_type=PROCESSOR_TYPE_FACE_EMBEDDING),
                "face_embedding": {"max_faces": 1000},
            }
        },
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


def test_claim_rejects_a_missing_job_shape_as_a_contract_error() -> None:
    with pytest.raises(ContractError):
        Claim.from_response({"empty": False, "job": {}})


def test_redaction_removes_urls_and_bearer_secrets_from_log_values() -> None:
    value = "GET https://storage.example.test/x?X-Amz-Signature=secret token worker-secret"

    redacted = redact(value, secrets=("worker-secret",))

    assert "storage.example" not in redacted
    assert "worker-secret" not in redacted
    assert "<redacted>" in redacted


def selfie_claim_payload(
    *, content_type: str = "image/png", **overrides: object
) -> dict[str, object]:
    configuration = processor_configuration(PROCESSOR_TYPE_SELFIE_QUERY)
    configuration.pop("face_embedding")
    configuration["worker"] = {
        **configuration["worker"],
        "max_input_bytes": 20 * 1024 * 1024,
        "max_pixels": 25_000_000,
    }
    configuration["selfie_query"] = {
        "detection_threshold": 0.75,
        "embedding_dimensions": 128,
        "min_face_px": 32,
        "model": "sface",
    }
    job: dict[str, object] = {
        "id": "00000000-0000-0000-0000-000000000011",
        "attempt_id": "00000000-0000-0000-0000-000000000012",
        "contract_version": 1,
        "processor_type": PROCESSOR_TYPE_SELFIE_QUERY,
        "processor_version": 1,
        "configuration": configuration,
        "event_id": "17",
        "search_id": "00000000-0000-0000-0000-000000000013",
        "input_fingerprint": {
            "temporary_key": "selfie-search/0123456789abcdef0123456789abcdef",
            "temporary_size": 1024,
            "temporary_content_type": content_type,
        },
        "input_limits": {"max_bytes": 1024, "content_type": content_type},
        "lease_expires_at": "2026-07-29T10:03:00+00:00",
        "download_url": "https://storage.example.test/selfie?X-Amz-Signature=secret",
        "download_expires_at": "2026-07-29T10:01:00+00:00",
    }
    return {"empty": False, "job": job | overrides}


def test_claim_accepts_only_the_exact_selfie_query_union_variant() -> None:
    claim = Claim.from_response(selfie_claim_payload())

    assert claim.job is not None
    assert claim.job.processor_type == PROCESSOR_TYPE_SELFIE_QUERY
    assert claim.job.event_id == "17"
    assert claim.job.search_id == "00000000-0000-0000-0000-000000000013"
    assert claim.job.input_fingerprint.temporary_content_type == "image/png"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_input_bytes", 20 * 1024 * 1024 - 1),
        ("max_pixels", 25_000_000 - 1),
    ],
)
def test_selfie_claim_rejects_limits_that_do_not_exactly_match_the_approved_contract(
    field: str, value: int
) -> None:
    payload = selfie_claim_payload()
    job = payload["job"]
    assert isinstance(job, dict)
    configuration = job["configuration"]
    assert isinstance(configuration, dict)
    worker = configuration["worker"]
    assert isinstance(worker, dict)
    worker[field] = value

    with pytest.raises(ContractError):
        Claim.from_response(payload)


@pytest.mark.parametrize(
    "payload",
    [
        selfie_claim_payload(photo_id="photo-1"),
        selfie_claim_payload(
            input_fingerprint={"temporary_key": "selfie-search/0123456789abcdef0123456789abcdef"}
        ),
        selfie_claim_payload(
            input_fingerprint={
                "temporary_key": "originals/0123456789abcdef0123456789abcdef",
                "temporary_size": 1024,
                "temporary_content_type": "image/jpeg",
            }
        ),
        selfie_claim_payload(content_type="image/gif"),
        selfie_claim_payload(
            input_limits={"max_bytes": 20 * 1024 * 1024 + 1, "content_type": "image/jpeg"}
        ),
        selfie_claim_payload(configuration={}),
    ],
)
def test_selfie_claim_rejects_mixed_or_unbounded_temporary_payloads(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ContractError):
        Claim.from_response(payload)
