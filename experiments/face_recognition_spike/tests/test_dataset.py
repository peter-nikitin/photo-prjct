from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from face_spike.dataset import load_image, load_labels
from face_spike.domain import (
    DatasetError,
    DatasetItem,
    FaceExpected,
    ImageLimits,
)
from fixtures import make_jpeg as write_jpeg


@pytest.fixture
def make_jpeg(tmp_path: Path) -> Callable[..., Path]:
    def create(filename: str, **kwargs: object) -> Path:
        return write_jpeg(tmp_path / filename, **kwargs)

    return create


def write_labels(
    path: Path,
    rows: list[str],
    header: str = "filename,participant_group,face_expected",
) -> Path:
    path.write_text("\n".join([header, *rows, ""]), encoding="utf-8")
    return path


def limits(*, dimension: int = 12, pixels: int = 100) -> ImageLimits:
    return ImageLimits(maximum_dimension=dimension, maximum_pixels=pixels)


def item(filename: str) -> DatasetItem:
    return DatasetItem(
        filename=filename,
        participant_group="rider-01",
        face_expected=FaceExpected.YES,
    )


def assert_error(code: str, operation: Callable[[], object]) -> None:
    with pytest.raises(DatasetError) as error:
        operation()
    assert error.value.code == code
    assert str(error.value) == code


def test_load_labels_requires_the_exact_header_and_parses_the_enum(
    tmp_path: Path, make_jpeg: Callable[..., Path]
) -> None:
    make_jpeg("rider.jpg")
    labels = write_labels(tmp_path / "labels.csv", ["rider.jpg,rider-01,yes"])

    loaded = load_labels(labels, tmp_path)

    assert loaded == (DatasetItem("rider.jpg", "rider-01", FaceExpected.YES),)
    assert_error(
        "invalid_labels",
        lambda: load_labels(
            write_labels(
                tmp_path / "wrong-header.csv",
                ["rider.jpg,rider-01,yes"],
                header="participant_group,filename,face_expected",
            ),
            tmp_path,
        ),
    )


def test_load_labels_rejects_unknown_face_expected_value(
    tmp_path: Path, make_jpeg: Callable[..., Path]
) -> None:
    make_jpeg("rider.jpg")

    assert_error(
        "invalid_labels",
        lambda: load_labels(
            write_labels(tmp_path / "labels.csv", ["rider.jpg,rider-01,maybe"]), tmp_path
        ),
    )


def test_load_labels_normalizes_blank_group_and_sorts_by_filename(
    tmp_path: Path, make_jpeg: Callable[..., Path]
) -> None:
    make_jpeg("zebra.jpg")
    make_jpeg("alpha.jpeg")

    loaded = load_labels(
        write_labels(
            tmp_path / "labels.csv",
            ["zebra.jpg,  ,no", "alpha.jpeg,rider-01,uncertain"],
        ),
        tmp_path,
    )

    assert [entry.filename for entry in loaded] == ["alpha.jpeg", "zebra.jpg"]
    assert loaded[1].participant_group is None
    assert loaded[0].face_expected is FaceExpected.UNCERTAIN


def test_load_labels_requires_a_group_for_yes_rows(
    tmp_path: Path, make_jpeg: Callable[..., Path]
) -> None:
    make_jpeg("rider.jpg")

    assert_error(
        "invalid_labels",
        lambda: load_labels(write_labels(tmp_path / "labels.csv", ["rider.jpg,,yes"]), tmp_path),
    )


def test_load_labels_rejects_duplicates_and_missing_files(
    tmp_path: Path, make_jpeg: Callable[..., Path]
) -> None:
    make_jpeg("rider.jpg")

    assert_error(
        "invalid_labels",
        lambda: load_labels(
            write_labels(tmp_path / "duplicates.csv", ["rider.jpg,rider-01,yes", "rider.jpg,,no"]),
            tmp_path,
        ),
    )
    assert_error(
        "invalid_labels",
        lambda: load_labels(
            write_labels(tmp_path / "missing.csv", ["missing.jpg,rider-01,yes"]), tmp_path
        ),
    )


def test_load_labels_rejects_header_only_file(tmp_path: Path) -> None:
    assert_error(
        "invalid_labels",
        lambda: load_labels(write_labels(tmp_path / "labels.csv", []), tmp_path),
    )


@pytest.mark.parametrize("filename", ["photo.png", "PHOTO.PNG"])
def test_load_labels_rejects_existing_unsupported_extensions(tmp_path: Path, filename: str) -> None:
    (tmp_path / filename).write_bytes(b"not an image")

    assert_error(
        "invalid_labels",
        lambda: load_labels(
            write_labels(tmp_path / "labels.csv", [f"{filename},rider-01,yes"]), tmp_path
        ),
    )


@pytest.mark.parametrize("filename", ["/outside.jpg", "../outside.jpg"])
def test_load_labels_rejects_absolute_and_parent_traversal_paths(
    tmp_path: Path, filename: str
) -> None:
    assert_error(
        "unsafe_path",
        lambda: load_labels(
            write_labels(tmp_path / "labels.csv", [f"{filename},rider-01,yes"]),
            tmp_path,
        ),
    )


def test_load_labels_rejects_symlink_escaping_the_photo_root(
    tmp_path: Path, make_jpeg: Callable[..., Path]
) -> None:
    outside = tmp_path.parent / "outside.jpg"
    make_jpeg(str(outside))
    (tmp_path / "escape.jpg").symlink_to(outside)

    assert_error(
        "unsafe_path",
        lambda: load_labels(
            write_labels(tmp_path / "labels.csv", ["escape.jpg,rider-01,yes"]), tmp_path
        ),
    )


def test_load_labels_rejects_directory_posing_as_an_image(tmp_path: Path) -> None:
    (tmp_path / "folder.jpg").mkdir()

    assert_error(
        "invalid_labels",
        lambda: load_labels(
            write_labels(tmp_path / "labels.csv", ["folder.jpg,rider-01,yes"]), tmp_path
        ),
    )


def test_load_image_rejects_unsupported_extensions(tmp_path: Path) -> None:
    (tmp_path / "photo.png").write_bytes(b"not an image")

    assert_error("unsupported_image", lambda: load_image(item("photo.png"), tmp_path, limits()))


def test_load_image_applies_exif_orientation_six_and_returns_contiguous_rgb_and_bgr(
    tmp_path: Path, make_jpeg: Callable[..., Path]
) -> None:
    make_jpeg("oriented.jpg", size=(3, 2), orientation=6)

    loaded = load_image(item("oriented.jpg"), tmp_path, limits())

    assert (loaded.width, loaded.height) == (2, 3)
    assert loaded.rgb.shape == (3, 2, 3)
    assert loaded.rgb.flags.c_contiguous
    assert loaded.bgr.flags.c_contiguous
    assert np.array_equal(loaded.bgr, loaded.rgb[:, :, ::-1])


def test_load_image_reports_corrupt_jpeg_without_leaking_its_path(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.jpg"
    corrupt.write_bytes(b"not a jpeg")

    with pytest.raises(DatasetError) as error:
        load_image(item("corrupt.jpg"), tmp_path, limits())

    assert error.value.code == "image_decode_failed"
    assert str(corrupt) not in str(error.value)


@pytest.mark.parametrize("size", [(13, 1), (1, 13)])
def test_load_image_rejects_dimensions_over_the_limit(
    tmp_path: Path, make_jpeg: Callable[..., Path], size: tuple[int, int]
) -> None:
    make_jpeg("large.jpg", size=size)

    assert_error("image_too_large", lambda: load_image(item("large.jpg"), tmp_path, limits()))


def test_load_image_rejects_decoded_pixels_over_the_limit(
    tmp_path: Path, make_jpeg: Callable[..., Path]
) -> None:
    make_jpeg("large.jpg", size=(10, 10))

    assert_error(
        "image_too_large",
        lambda: load_image(item("large.jpg"), tmp_path, limits(dimension=10, pixels=99)),
    )
