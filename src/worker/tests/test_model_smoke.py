from pathlib import Path

import pytest
from photo_worker import model_smoke, runtime_contract
from photo_worker.contracts import FaceEmbeddingResult
from photo_worker.face_embedding import FaceEmbeddingError


def test_model_smoke_passes_the_scrfd_threshold_to_both_consumers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Omitting the explicit smoke threshold would weaken the production-model check."""
    source = tmp_path / "no-face.jpg"
    source.write_bytes(b"jpeg")
    calls: dict[str, float] = {}

    def extract_photo(_path: Path, **kwargs: object) -> FaceEmbeddingResult:
        calls["photo"] = float(kwargs["detection_threshold"])
        return FaceEmbeddingResult(
            model="sface",
            faces=(),
            has_single_query_face_usable=False,
            warnings=("no_faces_detected",),
            timings={
                "decode_ms": 1,
                "model_load_ms": 1,
                "detect_ms": 1,
                "embed_ms": 0,
                "total_ms": 3,
            },
        )

    def extract_selfie(_path: Path, **kwargs: object) -> None:
        calls["selfie"] = float(kwargs["detection_threshold"])
        raise FaceEmbeddingError("no_face_detected")

    monkeypatch.setattr(model_smoke, "extract_face_embeddings", extract_photo)
    monkeypatch.setattr(model_smoke, "extract_selfie_embedding", extract_selfie)

    model_smoke._assert_photo_embedding_no_face(source)
    model_smoke._assert_selfie_query_no_face(source)

    assert calls == {"photo": 0.5, "selfie": 0.5}


def test_runtime_contract_accepts_the_exact_packaged_watermark_assets() -> None:
    """Replacing either committed placeholder without its declared checksum must stop the build."""

    class Torch:
        class version:
            cuda = None

    runtime_contract.verify_runtime_contract(
        torch_module=Torch(),
        site_package_directories=(),
        model_directories=(),
        distributions=(),
    )
