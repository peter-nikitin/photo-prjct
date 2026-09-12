"""Frozen JSON response contracts for photo-import API v1."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Final, Literal

IMPORT_CONTRACT_VERSION: Final = 1
IMPORT_MIN_JSON_BYTES: Final = 256
IMPORT_MAX_PAGE_ITEMS: Final = 100
IMPORT_MAX_JSON_BYTES: Final = 1024 * 1024

WORKER_CLAIM_ENDPOINT: Final = "claim"
WORKER_RENEW_ENDPOINT: Final = "attempts/{attempt_id}/renew"
WORKER_MANIFEST_PAGE_ENDPOINT: Final = "attempts/{attempt_id}/manifest/pages"
WORKER_MANIFEST_FINALIZE_ENDPOINT: Final = "attempts/{attempt_id}/manifest/finalize"
WORKER_PREPARE_UPLOAD_ENDPOINT: Final = "attempts/{attempt_id}/prepare-upload"
WORKER_COMPLETE_ENDPOINT: Final = "attempts/{attempt_id}/complete"
WORKER_FAIL_ENDPOINT: Final = "attempts/{attempt_id}/fail"


@dataclass(frozen=True, slots=True)
class ImportErrorResponse:
    code: str
    message: str
    retryable: bool | None = None
    contract_version: int = IMPORT_CONTRACT_VERSION

    def payload(self) -> dict[str, object]:
        error: dict[str, object] = {"code": self.code, "message": self.message}
        if self.retryable is not None:
            error["retryable"] = self.retryable
        return {"contract_version": self.contract_version, "error": error}


@dataclass(frozen=True, slots=True)
class WorkerClaimResponse:
    work: dict[str, Any] | None
    contract_version: int = IMPORT_CONTRACT_VERSION

    def payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ManifestPageResponse:
    page_number: int
    replayed: bool
    contract_version: int = IMPORT_CONTRACT_VERSION

    def payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "page": {"page_number": self.page_number, "replayed": self.replayed},
        }


@dataclass(frozen=True, slots=True)
class LeaseResponse:
    attempt_id: str
    kind: Literal["manifest", "file"]
    lease_expires_at: str
    contract_version: int = IMPORT_CONTRACT_VERSION

    def payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "attempt": {
                "attempt_id": self.attempt_id,
                "kind": self.kind,
                "lease_expires_at": self.lease_expires_at,
            },
        }


@dataclass(frozen=True, slots=True)
class ImportBatchResponse:
    batch: dict[str, Any]
    contract_version: int = IMPORT_CONTRACT_VERSION

    def payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UploadPreparationResponse:
    upload: dict[str, Any]
    contract_version: int = IMPORT_CONTRACT_VERSION

    def payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ImportCompletionResponse:
    item_id: str
    status: str
    photo_id: str | None
    contract_version: int = IMPORT_CONTRACT_VERSION

    def payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "completion": {
                "item_id": self.item_id,
                "status": self.status,
                "photo_id": self.photo_id,
            },
        }


@dataclass(frozen=True, slots=True)
class ImportFailureResponse:
    batch_id: str
    status: str
    contract_version: int = IMPORT_CONTRACT_VERSION

    def payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "failure": {"batch_id": self.batch_id, "status": self.status},
        }
