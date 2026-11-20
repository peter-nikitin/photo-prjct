import pytest
from photo_worker.contracts import PersonAppearanceError
from photo_worker.person_appearance import associate_heads, extract_person_appearance, focus_quality
from PIL import Image


def test_flat_person_has_no_descriptor():
    assert focus_quality(Image.new("RGB", (100, 300), "gray"))[0] == "lack_details"


def test_head_in_overlapping_people_has_no_unique_owner():
    bodies = [(0, 0, 100, 300), (20, 0, 120, 300)]
    assert associate_heads(bodies, [(40, 10, 60, 35)]) == {}
    assert associate_heads(bodies, [(2, 10, 14, 35)]) == {0: 0}


def test_second_detected_head_makes_person_link_unavailable():
    assert associate_heads([(0, 0, 160, 300)], [(2, 10, 14, 35), (52, 10, 64, 35)]) == {}


def test_corrupted_bytes_fail_before_loading_models(tmp_path):
    path = tmp_path / "preview.jpg"
    path.write_bytes(b"not the accepted preview")
    with pytest.raises(PersonAppearanceError, match="fingerprint_mismatch"):
        extract_person_appearance(path, max_pixels=2_560_000, sha256="a" * 64, size=(100, 300))
