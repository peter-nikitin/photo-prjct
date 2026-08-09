from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from .fixtures import make_jpeg, write_json


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _source_manifest(originals: Path, rows: list[tuple[str, Path]]) -> Path:
    files = [
        {
            "photo_id": photo_id,
            "filename": source.name,
            "key": f"originals/{photo_id}",
            "content_type": "image/jpeg",
            "etag": f'"{photo_id}"',
            "size": source.stat().st_size,
            "sha256": _sha256(source),
        }
        for photo_id, source in rows
    ]
    inventory = [
        {key: item[key] for key in ("photo_id", "filename", "key", "size", "content_type", "etag")}
        for item in files
    ]
    manifest: dict[str, object] = {
        "version": 1,
        "complete": True,
        "event": {"id": "9", "slug": "test-event"},
        "files": files,
        "inventory_hash": _canonical_sha256(inventory),
        "unresolved_count": 0,
    }
    manifest["manifest_hash"] = _canonical_sha256(manifest)
    return write_json(originals.parent / "source-manifest.json", manifest)


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    originals = tmp_path / "originals"
    first = make_jpeg(originals / "photo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg", size=(20, 10))
    second = make_jpeg(
        originals / "photo-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg",
        size=(4, 9),
        orientation=6,
    )
    manifest = _source_manifest(
        originals,
        [
            ("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", second),
            ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", first),
        ],
    )
    return manifest, originals, tmp_path / "previews"


def test_materialize_maps_sorted_photo_ids_and_freezes_oriented_evidence(tmp_path: Path) -> None:
    """Changing source-to-output mapping or dropping EXIF orientation breaks evidence."""
    from face_spike.preview_corpus import materialize_preview_corpus

    source, originals, output = _setup(tmp_path)

    manifest = materialize_preview_corpus(source, originals, output, workers=2)

    assert [entry.photo_id for entry in manifest.photos] == [
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]
    assert [entry.preview_filename for entry in manifest.photos] == [
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg",
    ]
    assert manifest.photos[1].oriented_source_width == 9
    assert manifest.photos[1].oriented_source_height == 4
    assert manifest.complete is True
    assert manifest.unresolved == ()
    payload = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert payload["manifest_sha256"] == manifest.manifest_sha256
    assert payload["source_manifest_sha256"] == _sha256(source)


def test_materialize_uses_production_preview_slot_limits(tmp_path: Path) -> None:
    """A different output slot would silently produce non-production preview bytes."""
    from face_spike.preview_corpus import materialize_preview_corpus

    source, originals, output = _setup(tmp_path)

    manifest = materialize_preview_corpus(source, originals, output, workers=1)

    assert manifest.preview_contract["variant"] == "preview-small-v1"
    assert manifest.preview_contract["max_long_edge"] == 1600
    assert manifest.preview_contract["jpeg_quality"] == 85
    assert manifest.preview_contract["max_output_bytes"] == 10_485_760
    assert all(entry.width <= 1600 and entry.height <= 1600 for entry in manifest.photos)


def test_source_mismatch_is_rejected_before_preview_publication(tmp_path: Path) -> None:
    """Generating from changed originals would make the frozen source evidence false."""
    from face_spike.preview_corpus import PreviewCorpusError, materialize_preview_corpus

    source, originals, output = _setup(tmp_path)
    changed = originals / "photo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"
    make_jpeg(changed, color=(1, 2, 3))

    with pytest.raises(PreviewCorpusError, match="source corpus"):
        materialize_preview_corpus(source, originals, output, workers=1)

    assert not output.exists()


@pytest.mark.parametrize("field", ["inventory_hash", "manifest_hash"])
def test_materialize_rejects_tampered_source_cache_digests(tmp_path: Path, field: str) -> None:
    """Cache digest tampering must be rejected before source files are trusted."""
    from face_spike.preview_corpus import PreviewCorpusError, materialize_preview_corpus

    source, originals, output = _setup(tmp_path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload[field] = "0" * 64
    source.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(PreviewCorpusError, match="source corpus"):
        materialize_preview_corpus(source, originals, output, workers=1)


def test_materialize_rejects_empty_completed_source_etag(tmp_path: Path) -> None:
    """A completed cache row without its ETag is not source identity evidence."""
    from face_spike.preview_corpus import PreviewCorpusError, materialize_preview_corpus

    source, originals, output = _setup(tmp_path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["files"][0]["etag"] = ""
    inventory = [
        {key: item[key] for key in ("photo_id", "filename", "key", "size", "content_type", "etag")}
        for item in payload["files"]
    ]
    payload["inventory_hash"] = _canonical_sha256(inventory)
    payload["manifest_hash"] = _canonical_sha256(
        {key: value for key, value in payload.items() if key != "manifest_hash"}
    )
    source.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(PreviewCorpusError, match="source corpus"):
        materialize_preview_corpus(source, originals, output, workers=1)


@pytest.mark.parametrize("kind", ["unexpected", "symlink"])
def test_materialize_rejects_undeclared_or_symlinked_originals(tmp_path: Path, kind: str) -> None:
    """Inventory must remain direct-only and exactly equal to the frozen manifest."""
    from face_spike.preview_corpus import PreviewCorpusError, materialize_preview_corpus

    source, originals, output = _setup(tmp_path)
    if kind == "unexpected":
        make_jpeg(originals / "photo-cccccccccccccccccccccccccccccccc.jpg")
    else:
        (originals / "photo-cccccccccccccccccccccccccccccccc.jpg").symlink_to(
            originals / "photo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"
        )

    with pytest.raises(PreviewCorpusError, match="source corpus"):
        materialize_preview_corpus(source, originals, output, workers=1)

    assert not output.exists()


def test_failure_leaves_incomplete_attempt_evidence_without_complete_manifest(
    tmp_path: Path,
) -> None:
    """A decode failure must be inspectable but never look like a usable corpus."""
    from face_spike.preview_corpus import PreviewCorpusError, materialize_preview_corpus

    source, originals, output = _setup(tmp_path)
    broken = originals / "photo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"
    broken.write_bytes(b"not a jpeg")
    source = _source_manifest(
        originals,
        [
            ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", broken),
            (
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                originals / "photo-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg",
            ),
        ],
    )

    with pytest.raises(PreviewCorpusError, match="preview generation failed"):
        materialize_preview_corpus(source, originals, output, workers=1)

    failure = json.loads((output / "incomplete-manifest.json").read_text(encoding="utf-8"))
    assert failure["complete"] is False
    assert failure["unresolved"] == [
        {"photo_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "error": "decode_failed"}
    ]
    assert [item["photo_id"] for item in failure["photos"]] == ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
    assert failure["manifest_sha256"] == _canonical_sha256(
        {key: value for key, value in failure.items() if key != "manifest_sha256"}
    )
    assert not (output / "manifest.json").exists()


def test_transient_failure_resumes_from_canonical_incomplete_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restarting a failed corpus must reuse verified bytes and regenerate only unresolved work."""
    from face_spike import preview_corpus
    from photo_worker.preview import PreviewError

    source, originals, output = _setup(tmp_path)
    real_generate = preview_corpus.generate_preview
    failed_once = True

    def fail_once(*args: object, **kwargs: object) -> object:
        nonlocal failed_once
        original = args[0]
        if failed_once and isinstance(original, Path) and original.name.startswith("photo-aaaa"):
            failed_once = False
            raise PreviewError("decode_failed")
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(preview_corpus, "generate_preview", fail_once)
    with pytest.raises(preview_corpus.PreviewCorpusError, match="preview generation failed"):
        preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)

    incomplete = json.loads((output / "incomplete-manifest.json").read_text(encoding="utf-8"))
    assert incomplete["complete"] is False
    assert [item["photo_id"] for item in incomplete["photos"]] == [
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    ]
    assert incomplete["unresolved"] == [
        {"error": "decode_failed", "photo_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
    ]
    preserved = (output / "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg").read_bytes()

    result = preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)

    assert result.generated == 1
    assert result.reused == 1
    assert (output / "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg").read_bytes() == preserved
    assert not (output / "incomplete-manifest.json").exists()
    assert (output / "manifest.json").is_file()


def test_resume_rejects_self_consistent_incomplete_evidence_with_wrong_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resumable attempt still fails closed if its frozen source identity changes."""
    from face_spike import preview_corpus
    from photo_worker.preview import PreviewError

    source, originals, output = _setup(tmp_path)

    def fail_all(*args: object, **kwargs: object) -> object:
        raise PreviewError("decode_failed")

    monkeypatch.setattr(preview_corpus, "generate_preview", fail_all)
    with pytest.raises(preview_corpus.PreviewCorpusError):
        preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)

    path = output / "incomplete-manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_manifest_sha256"] = "0" * 64
    frozen = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    payload["manifest_sha256"] = _canonical_sha256(frozen)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(preview_corpus.PreviewCorpusError, match="incomplete"):
        preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)


def test_verified_rerun_reuses_only_matching_frozen_file_evidence(tmp_path: Path) -> None:
    """Reuse must fail closed if an output byte changes after the original run."""
    from face_spike.preview_corpus import PreviewCorpusError, materialize_preview_corpus

    source, originals, output = _setup(tmp_path)
    first = materialize_preview_corpus(source, originals, output, workers=1)
    second = materialize_preview_corpus(source, originals, output, workers=2)

    assert first.manifest_sha256 == second.manifest_sha256
    assert second.generated == 0
    assert second.reused == 2
    (output / "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg").write_bytes(b"changed")

    with pytest.raises(PreviewCorpusError, match="output corpus"):
        materialize_preview_corpus(source, originals, output, workers=1)


def test_load_verified_rejects_missing_or_tampered_preview(tmp_path: Path) -> None:
    """Consumers must not treat a marker as proof when a preview changed or vanished."""
    from face_spike.preview_corpus import (
        PreviewCorpusError,
        load_verified_preview_corpus,
        materialize_preview_corpus,
    )

    source, originals, output = _setup(tmp_path)
    materialize_preview_corpus(source, originals, output, workers=1)
    (output / "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg").unlink()

    with pytest.raises(PreviewCorpusError, match="output corpus"):
        load_verified_preview_corpus(output)


def test_load_verified_rejects_self_consistent_false_dimensions(tmp_path: Path) -> None:
    """Reuse must replay JPEG dimensions instead of trusting a manifest row."""
    from face_spike.preview_corpus import (
        PreviewCorpusError,
        load_verified_preview_corpus,
        materialize_preview_corpus,
    )

    source, originals, output = _setup(tmp_path)
    materialize_preview_corpus(source, originals, output, workers=1)
    path = output / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["photos"][0]["width"] += 1
    payload["manifest_sha256"] = _canonical_sha256(
        {key: value for key, value in payload.items() if key != "manifest_sha256"}
    )
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(PreviewCorpusError, match="output corpus"):
        load_verified_preview_corpus(output)


def test_generate_one_closes_the_mkstemp_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The materializer must not retain one descriptor for each generated preview."""
    from face_spike import preview_corpus

    source, originals, output = _setup(tmp_path)
    output.mkdir()
    source_photo = preview_corpus._SourcePhoto(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "photo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
        _sha256(originals / "photo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"),
        (originals / "photo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg").stat().st_size,
        originals / "photo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
    )
    real_mkstemp = preview_corpus.tempfile.mkstemp
    descriptors: list[int] = []

    def capture_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        descriptor, name = real_mkstemp(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor, name

    monkeypatch.setattr(preview_corpus.tempfile, "mkstemp", capture_mkstemp)
    preview_corpus._generate_one(source_photo, output)

    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_generation_uses_exact_slot_and_atomically_publishes_after_preview_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production slot fields and same-filesystem partial publication are observable contracts."""
    from face_spike import preview_corpus
    from photo_worker.contracts import MAX_PREVIEW_PIXELS_CAP, V2_GENERATE_PREVIEW_CONFIGURATION
    from photo_worker.preview import PreviewResult

    source, originals, output = _setup(tmp_path)
    observed_slots = []
    events: list[str] = []
    real_replace = preview_corpus.os.replace

    def fake_generate(
        original: Path,
        partial: Path,
        *,
        max_input_bytes: int,
        max_pixels: int,
        slot: object,
    ) -> PreviewResult:
        assert original.parent == originals
        assert partial.parent == output
        assert partial.suffix == ".partial"
        assert not (output / f"{original.stem.removeprefix('photo-')}.jpg").exists()
        assert not (output / "manifest.json").exists()
        assert max_input_bytes == V2_GENERATE_PREVIEW_CONFIGURATION["worker"]["max_input_bytes"]
        assert max_pixels == MAX_PREVIEW_PIXELS_CAP
        observed_slots.append(slot)
        make_jpeg(partial, size=(5, 4))
        events.append("generated")
        return PreviewResult(
            "preview-small-v1",
            "image/jpeg",
            partial.stat().st_size,
            5,
            4,
            5,
            4,
            _sha256(partial),
            (),
        )

    def observe_replace(source_path: Path, destination: Path) -> None:
        if source_path.suffix == ".partial" and destination.suffix == ".jpg":
            assert events[-1] == "generated"
            assert not (output / "manifest.json").exists()
            events.append("published")
        real_replace(source_path, destination)

    monkeypatch.setattr(preview_corpus, "generate_preview", fake_generate)
    monkeypatch.setattr(preview_corpus.os, "replace", observe_replace)
    preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)

    config = V2_GENERATE_PREVIEW_CONFIGURATION["generate_preview"]
    assert len(observed_slots) == 2
    for slot in observed_slots:
        assert slot.variant == config["variant"]
        assert slot.content_type == "image/jpeg"
        assert slot.max_bytes == config["max_output_bytes"]
        assert slot.max_width == config["max_output_width"]
        assert slot.max_height == config["max_output_height"]
        assert slot.checksum_algorithm == config["checksum_algorithm"]
        assert slot.staging_key in {
            "processing-staging/previews/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/preview-small-v1.jpg",
            "processing-staging/previews/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/preview-small-v1.jpg",
        }
    assert events == ["generated", "published", "generated", "published"]


def test_final_verification_failure_preserves_resumable_terminal_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A last-step verification failure must retain verified bytes without publishing completion."""
    from face_spike import preview_corpus

    source, originals, output = _setup(tmp_path)

    def fail_final(*args: object, **kwargs: object) -> None:
        raise preview_corpus.PreviewCorpusError("forced final verification failure")

    monkeypatch.setattr(preview_corpus, "_verify_manifest_files", fail_final)
    with pytest.raises(
        preview_corpus.PreviewCorpusError, match="output corpus verification failed"
    ):
        preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)

    incomplete = json.loads((output / "incomplete-manifest.json").read_text(encoding="utf-8"))
    assert incomplete["complete"] is False
    assert [item["photo_id"] for item in incomplete["photos"]] == [
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]
    assert incomplete["unresolved"] == [
        {"error": "final_verification_failed", "photo_id": "<finalization>"}
    ]
    assert not (output / "manifest.json").exists()

    monkeypatch.undo()
    resumed = preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)

    assert resumed.generated == 0
    assert resumed.reused == 2
    assert (output / "manifest.json").is_file()


def test_stale_incomplete_marker_is_removed_only_after_complete_recovery_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interruption after atomic completion leaves a recoverable verified state."""
    from face_spike import preview_corpus

    source, originals, output = _setup(tmp_path)
    real_generate = preview_corpus.generate_preview
    failed_once = True

    def create_incomplete(*args: object, **kwargs: object) -> object:
        nonlocal failed_once
        if failed_once:
            failed_once = False
            raise preview_corpus.PreviewError("decode_failed")
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(preview_corpus, "generate_preview", create_incomplete)
    with pytest.raises(preview_corpus.PreviewCorpusError):
        preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)
    monkeypatch.setattr(preview_corpus, "generate_preview", real_generate)
    real_unlink = Path.unlink

    def interrupt_marker_removal(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == "incomplete-manifest.json":
            raise OSError("forced interruption")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt_marker_removal)
    with pytest.raises(
        preview_corpus.PreviewCorpusError, match="output corpus verification failed"
    ):
        preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)

    assert (output / "manifest.json").is_file()
    assert (output / "incomplete-manifest.json").is_file()
    monkeypatch.undo()

    recovered = preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)

    assert recovered.generated == 0
    assert recovered.reused == 2
    assert not (output / "incomplete-manifest.json").exists()


def test_complete_recovery_rejects_mismatched_stale_incomplete_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale marker is removable only when it proves the same frozen source evidence."""
    from face_spike import preview_corpus

    source, originals, output = _setup(tmp_path)
    real_generate = preview_corpus.generate_preview
    failed_once = True

    def create_incomplete(*args: object, **kwargs: object) -> object:
        nonlocal failed_once
        if failed_once:
            failed_once = False
            raise preview_corpus.PreviewError("decode_failed")
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(preview_corpus, "generate_preview", create_incomplete)
    with pytest.raises(preview_corpus.PreviewCorpusError):
        preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)
    monkeypatch.setattr(preview_corpus, "generate_preview", real_generate)
    real_unlink = Path.unlink

    def interrupt_marker_removal(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == "incomplete-manifest.json":
            raise OSError("forced interruption")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt_marker_removal)
    with pytest.raises(preview_corpus.PreviewCorpusError):
        preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)
    monkeypatch.undo()

    path = output / "incomplete-manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_manifest_sha256"] = "0" * 64
    payload["manifest_sha256"] = _canonical_sha256(
        {key: value for key, value in payload.items() if key != "manifest_sha256"}
    )
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(preview_corpus.PreviewCorpusError, match="incomplete"):
        preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)


def test_final_corrupt_preview_is_removed_before_resumable_incomplete_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad final preview cannot remain beside evidence that says it must be regenerated."""
    from face_spike import preview_corpus

    source, originals, output = _setup(tmp_path)
    real_verify = preview_corpus._verify_manifest_files
    corrupted = output / "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"

    def corrupt_before_final_verification(
        root: Path, manifest: preview_corpus.PreviewCorpusManifest
    ) -> None:
        corrupted.write_bytes(b"corrupted preview")
        real_verify(root, manifest)

    monkeypatch.setattr(preview_corpus, "_verify_manifest_files", corrupt_before_final_verification)
    with pytest.raises(
        preview_corpus.PreviewCorpusError, match="output corpus verification failed"
    ):
        preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)

    incomplete = json.loads((output / "incomplete-manifest.json").read_text(encoding="utf-8"))
    assert [item["photo_id"] for item in incomplete["photos"]] == [
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    ]
    assert incomplete["unresolved"] == [
        {"error": "final_file_verification_failed", "photo_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
    ]
    assert not corrupted.exists()

    monkeypatch.undo()
    resumed = preview_corpus.materialize_preview_corpus(source, originals, output, workers=1)

    assert resumed.generated == 1
    assert resumed.reused == 1
    assert (output / "manifest.json").is_file()


def test_load_verified_rejects_a_nonproduction_contract_even_with_a_valid_hash(
    tmp_path: Path,
) -> None:
    """A self-consistent manifest cannot substitute a different preview contract."""
    from face_spike.preview_corpus import PreviewCorpusError, load_verified_preview_corpus

    source, originals, output = _setup(tmp_path)
    from face_spike.preview_corpus import materialize_preview_corpus

    materialize_preview_corpus(source, originals, output, workers=1)
    manifest_path = output / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["preview_contract"]["jpeg_quality"] = 1
    payload["production_contract_sha256"] = hashlib.sha256(
        json.dumps(
            payload["preview_contract"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    frozen = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(
            frozen,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    manifest_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(PreviewCorpusError, match="output corpus"):
        load_verified_preview_corpus(output)
