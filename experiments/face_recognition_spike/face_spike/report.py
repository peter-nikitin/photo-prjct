from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from html import escape
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from face_spike.artifacts import asset_relative_path
from face_spike.pipeline import ImageAnalysis

if TYPE_CHECKING:
    from face_spike.artifacts import ExperimentResult


def render_report(result: ExperimentResult, metrics: Mapping[str, Any]) -> str:
    """Render a deterministic, static review page with no biometric vectors."""
    analyses = tuple(sorted(result.analyses, key=lambda analysis: analysis.image.item.filename))
    body = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8"><title>Face spike report</title></head><body>',
        "<h1>Face spike report</h1>",
        "<h2>Summary metrics</h2>",
        _metrics_table(metrics),
        "<h2>Face-size buckets</h2>",
        _bucket_table(metrics.get("face_size_buckets", {})),
        "<h2>Failures</h2>",
        _failure_table(analyses),
        "<h2>Evaluable queries</h2>",
        _query_sections(result, analyses),
        "</body></html>",
    ]
    return "\n".join(body) + "\n"


def _metrics_table(metrics: Mapping[str, Any]) -> str:
    values = {
        "Detection coverage": metrics.get("detection", {}).get("expected_face_coverage"),
        "False detections": metrics.get("detection", {}).get("false_detections"),
        "Embedding success": metrics.get("counts", {}).get("embedding_success"),
        "Evaluable queries": metrics.get("retrieval", {}).get("evaluable_queries"),
        "Recall@1": metrics.get("retrieval", {}).get("recall_at_1"),
        "Recall@5": metrics.get("retrieval", {}).get("recall_at_5"),
        "Recall@10": metrics.get("retrieval", {}).get("recall_at_10"),
    }
    rows = "".join(
        f"<tr><th>{escape(name)}</th><td>{_value(value)}</td></tr>"
        for name, value in values.items()
    )
    return f"<table>{rows}</table>"


def _bucket_table(buckets: Mapping[str, Any]) -> str:
    labels = ("<32", "32–63", "64–127", "128–255", ">=256")
    keys = ("<32", "32-63", "64-127", "128-255", ">=256")
    rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{_value(buckets.get(key, 0))}</td></tr>"
        for label, key in zip(labels, keys, strict=True)
    )
    return f"<table>{rows}</table>"


def _failure_table(analyses: tuple[ImageAnalysis, ...]) -> str:
    failures = [analysis for analysis in analyses if analysis.status != "ok"]
    if not failures:
        return "<p>None</p>"
    rows = "".join(
        "<tr>"
        f"<td>{escape(analysis.image.item.filename)}</td>"
        f"<td>{escape(analysis.status)}</td>"
        "</tr>"
        for analysis in failures
    )
    return f"<table><tr><th>Filename</th><th>Status</th></tr>{rows}</table>"


def _query_sections(result: ExperimentResult, analyses: tuple[ImageAnalysis, ...]) -> str:
    group_counts = Counter(
        analysis.image.item.participant_group
        for analysis in analyses
        if analysis.status == "ok"
        and analysis.embedding is not None
        and analysis.image.item.participant_group
    )
    queries = [
        query
        for query in sorted(result.retrieval.queries, key=lambda entry: entry.filename)
        if query.participant_group is not None and group_counts[query.participant_group] > 1
    ]
    if not queries:
        return "<p>None</p>"
    sections: list[str] = []
    for query in queries:
        query_image = _image_tag("annotated", query.filename, ".jpg", query.filename)
        candidates = "".join(
            "<li>"
            f"{_image_tag('crops', match.filename, '.png', match.filename)} "
            f"{escape(match.filename)} — distance {_value(match.distance)}, "
            f"same group: {_value(match.same_group)}"
            "</li>"
            for match in query.matches[:10]
        )
        sections.append(
            "<section>"
            f"<h3>{escape(query.filename)} ({escape(query.participant_group)})</h3>"
            f"{query_image}<ol>{candidates}</ol>"
            "</section>"
        )
    return "\n".join(sections)


def _image_tag(directory: str, filename: str, suffix: str, alt: str) -> str:
    relative = asset_relative_path(filename, suffix).as_posix()
    url = quote(f"{directory}/{relative}", safe="/-._~")
    return f'<img src="{url}" alt="{escape(alt)}">'


def _value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, float):
        return format(value, ".6g")
    return escape(str(value))
