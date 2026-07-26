from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .analysis import EventPhotoAnalysis, face_crop_path
from .clustering import FaceCluster, ordered_face_clusters


def render_cluster_report(
    analyses: Sequence[EventPhotoAnalysis],
    clusters: Sequence[FaceCluster],
    metrics: Mapping[str, Any],
) -> str:
    """Render a deterministic static review surface with stable cluster anchors."""
    face_by_id = {
        face.face_id: face
        for analysis in sorted(analyses, key=lambda item: item.filename)
        for face in analysis.faces
    }
    ordered_clusters = ordered_face_clusters(clusters)
    navigation = "".join(
        f'<li><a href="#{escape(cluster.cluster_id)}">{escape(cluster.cluster_id)}</a></li>'
        for cluster in ordered_clusters
    )
    sections = "\n".join(_cluster_section(cluster, face_by_id) for cluster in ordered_clusters)
    counts = metrics.get("counts", {})
    summary = "".join(
        f"<tr><th>{escape(str(name))}</th><td>{escape(str(value))}</td></tr>"
        for name, value in sorted(counts.items())
    )
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">'
            "<title>Anonymous face clusters</title></head><body>",
            "<h1>Anonymous face clusters</h1>",
            f"<table>{summary}</table>",
            f"<nav><ul>{navigation}</ul></nav>",
            sections or "<p>No successfully embedded faces.</p>",
            "</body></html>",
            "",
        )
    )


def _cluster_section(cluster: FaceCluster, face_by_id: Mapping[str, Any]) -> str:
    member_rows: list[str] = []
    filenames: set[str] = set()
    for member in sorted(cluster.members, key=lambda item: item.face_id):
        face = face_by_id.get(member.face_id)
        filename = member.face_id.partition("#")[0] if face is None else face.filename
        filenames.add(filename)
        crop_path = face_crop_path(member.face_id) if face is None else face.crop_path
        face_url = quote(
            f"people/{cluster.cluster_id}/faces/{Path(crop_path).name}",
            safe="/-._~",
        )
        member_rows.append(
            "<li>"
            f'<img src="{face_url}" alt="{escape(member.face_id)}"> '
            f"{escape(member.face_id)} — distance "
            f"{format(member.distance_to_representative, '.12g')}"
            "</li>"
        )
    photos = "".join(
        "<li>"
        f'<img src="{quote(f"people/{cluster.cluster_id}/photos/{filename}", safe="/-._~")}" '
        f'alt="{escape(filename)}"> {escape(filename)}'
        "</li>"
        for filename in sorted(filenames)
    )
    return (
        f'<section id="{escape(cluster.cluster_id)}">'
        f"<h2>{escape(cluster.cluster_id)}</h2>"
        f"<p>Representative: {escape(cluster.representative_face_id)}</p>"
        f"<h3>Faces</h3><ul>{''.join(member_rows)}</ul>"
        f"<h3>Source photos</h3><ul>{photos}</ul>"
        "</section>"
    )
