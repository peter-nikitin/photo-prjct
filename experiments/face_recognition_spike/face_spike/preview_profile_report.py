"""Self-contained HTML for the seven-photo and sampled-change review surfaces."""
# ruff: noqa: E501

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import quote, unquote


def render_profile_report(
    problem_records: Sequence[Mapping[str, object]],
    sampled_changed: Sequence[Mapping[str, object]] = (),
) -> str:
    sections = "".join(_photo_section(record) for record in problem_records)
    cards = "".join(_changed_card(item) for item in sampled_changed)
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>Preview quality comparison</title>"
        "<style>body{font:16px sans-serif;margin:2rem}img{max-width:320px;margin:.3rem}"
        "pre{white-space:pre-wrap}</style></head><body>"
        "<h1>Preview quality comparison</h1><h2>Problem photos</h2>"
        f"{sections}<h2>Sampled changed decisions</h2>{cards}</body></html>\n"
    )


def validate_report_links(bundle: Path, page: Path) -> None:
    root = bundle.resolve()
    text = page.read_text(encoding="utf-8")
    for href in re.findall(r'(?:src|href)="([^"]+)"', text):
        if "://" in href or href.startswith("/"):
            raise ValueError("report link is not bundle-relative")
        target = (page.parent / unquote(href)).resolve()
        if root not in target.parents or not target.is_file() or target.is_symlink():
            raise ValueError("report link escapes or is missing from bundle")


def _photo_section(record: Mapping[str, object]) -> str:
    photo_id = html.escape(str(record["photo_id"]))
    preview = _href(str(record["preview_path"]))
    status = html.escape(str(record.get("status", "not-recorded")))
    payload = html.escape(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return (
        f'<section id="photo-{photo_id}"><h3>{photo_id}</h3><img src="{preview}" '
        f'alt="Preview {photo_id}"><p>Technical status: {status}</p><pre>{payload}</pre></section>'
    )


def _changed_card(item: Mapping[str, object]) -> str:
    crop = item.get("crop_path")
    if not isinstance(crop, str):
        raise ValueError("changed decision lacks crop")
    payload = html.escape(json.dumps(item, ensure_ascii=False, sort_keys=True))
    return (
        f'<article><img src="{_href(crop)}" alt="Changed face crop"><pre>{payload}</pre></article>'
    )


def _href(path: str) -> str:
    value = Path(path)
    if value.is_absolute() or ".." in value.parts or not path:
        raise ValueError("unsafe report media path")
    return quote(path, safe="/")
