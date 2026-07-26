from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from face_spike import inventory
from fixtures import make_jpeg as write_jpeg


@pytest.fixture
def make_jpeg(tmp_path: Path) -> Callable[..., Path]:
    def create(filename: str, **kwargs: object) -> Path:
        return write_jpeg(tmp_path / filename, **kwargs)

    return create


def test_event_photo_inventory_is_sorted_and_ignores_regular_sidecars(
    tmp_path: Path, make_jpeg: Callable[..., Path]
) -> None:
    make_jpeg("z-last.JPG")
    make_jpeg("a-first.jpeg")
    (tmp_path / ".DS_Store").write_bytes(b"sidecar")
    (tmp_path / "notes.png").write_bytes(b"sidecar")

    result = inventory.load_event_photo_inventory(tmp_path)

    assert [photo.filename for photo in result.photos] == ["a-first.jpeg", "z-last.JPG"]


@pytest.mark.parametrize("scenario", ["empty", "nested", "symlink", "case_collision"])
def test_event_photo_inventory_rejects_unsupported_or_ambiguous_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_jpeg: Callable[..., Path],
    scenario: str,
) -> None:
    if scenario == "nested":
        (tmp_path / "nested").mkdir()
    elif scenario == "symlink":
        make_jpeg("source.jpg")
        (tmp_path / "alias.jpg").symlink_to(tmp_path / "source.jpg")
    elif scenario == "case_collision":
        make_jpeg("face.jpg")

        class CaseCollidingJpeg:
            name = "FACE.JPG"
            suffix = ".JPG"

            def is_dir(self) -> bool:
                return False

            def is_symlink(self) -> bool:
                return False

            def is_file(self) -> bool:
                return True

        original_iterdir = Path.iterdir

        def with_case_collision(path: Path) -> object:
            if path == tmp_path:
                return iter((tmp_path / "face.jpg", CaseCollidingJpeg()))
            return original_iterdir(path)

        monkeypatch.setattr(inventory.Path, "iterdir", with_case_collision)

    with pytest.raises(inventory.InventoryError) as error:
        inventory.load_event_photo_inventory(tmp_path)

    assert error.value.code == "invalid_photo_inventory"
