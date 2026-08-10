# ruff: noqa: E501

from pathlib import Path

import pytest


def test_report_media_links_are_relative_and_contained(tmp_path: Path) -> None:
    from face_spike.preview_profile_report import render_profile_report, validate_report_links

    (tmp_path / "previews").mkdir()
    (tmp_path / "previews" / "one.jpg").write_bytes(b"preview")
    page = tmp_path / "report.html"
    page.write_text(
        render_profile_report(
            [{"photo_id": "one", "preview_path": "previews/one.jpg", "crop_path": None}]
        ),
        encoding="utf-8",
    )
    validate_report_links(tmp_path, page)


def test_report_percent_encodes_and_resolves_media_name(tmp_path: Path) -> None:
    from face_spike.preview_profile_report import render_profile_report, validate_report_links

    (tmp_path / "previews").mkdir()
    (tmp_path / "previews" / "face #?.jpg").write_bytes(b"preview")
    page = tmp_path / "report.html"
    page.write_text(
        render_profile_report([{"photo_id": "one", "preview_path": "previews/face #?.jpg"}]),
        encoding="utf-8",
    )
    assert "%23%3F" in page.read_text(encoding="utf-8")
    validate_report_links(tmp_path, page)


@pytest.mark.parametrize(
    "href", ["previews/missing.jpg", "../outside.jpg", "https://example.test/a.jpg"]
)
def test_report_rejects_missing_or_escaping_links(tmp_path: Path, href: str) -> None:
    from face_spike.preview_profile_report import validate_report_links

    page = tmp_path / "report.html"
    page.write_text(f'<img src="{href}">', encoding="utf-8")
    with pytest.raises(ValueError):
        validate_report_links(tmp_path, page)


def test_seven_problem_sections_and_sampled_changed_card_render() -> None:
    """J: operator report has every explicit problem section plus changed-decision evidence."""
    from face_spike.preview_profile_report import render_profile_report

    rows = [
        {
            "photo_id": f"{index:032x}",
            "preview_path": f"previews/{index}.jpg",
            "status": "ok",
            "thresholds": [],
        }
        for index in range(7)
    ]
    report = render_profile_report(
        rows,
        [{"photo_id": "sample", "crop_path": "crops/changed.png", "decision": "quality_rejected"}],
    )
    assert report.count('<section id="photo-') == 7
    assert "quality_rejected" in report and "crops/changed.png" in report
