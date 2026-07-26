from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from html import escape


def render_people_comparison(
    rows: Sequence[Mapping[str, object]],
    unmatched_clusters: Sequence[Mapping[str, object]],
    cluster_href: Callable[[str], str],
) -> str:
    """Render the deterministic static people comparison table."""
    headers = (
        tuple(rows[0])
        if rows
        else (
            "peakshot_person_id",
            "peakshot_photo_count",
            "matched_cluster_ids",
            "our_photo_count",
            "intersection_count",
            "missing_count",
            "extra_count",
            "precision",
            "recall",
        )
    )
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body_rows = "\n".join(
        "<tr>" + "".join(_cell(header, row[header], cluster_href) for header in headers) + "</tr>"
        for row in rows
    )
    unmatched_html = "\n".join(
        "<li>"
        f'<a href="{escape(cluster_href(str(cluster["cluster_id"])), quote=True)}">'
        f"{escape(str(cluster['cluster_id']))}</a> "
        f"({escape(str(cluster['photo_count']))} photos)"
        "</li>"
        for cluster in unmatched_clusters
    )
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">',
            "<title>Peakshot comparison</title></head><body>",
            "<h1>Peakshot comparison</h1>",
            "<p>Peakshot is an algorithmic silver-label reference; disagreement is evidence "
            "for review, not a confirmed recognition error.</p>",
            f"<table><thead><tr>{header_html}</tr></thead><tbody>{body_rows}</tbody></table>",
            "<h2>unmatched clusters</h2>",
            f"<ul>{unmatched_html}</ul>",
            "</body></html>",
            "",
        )
    )


def _cell(header: str, value: object, cluster_href: Callable[[str], str]) -> str:
    if header != "matched_cluster_ids":
        return f"<td>{escape(_display(value))}</td>"
    cluster_ids = str(value).split(";") if value else []
    links = "; ".join(
        f'<a href="{escape(cluster_href(cluster_id), quote=True)}">{escape(cluster_id)}</a>'
        for cluster_id in cluster_ids
    )
    return f"<td>{links}</td>"


def _display(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)
