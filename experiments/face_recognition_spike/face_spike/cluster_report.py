from __future__ import annotations

# ruff: noqa: E501, I001 -- static HTML and CSS are intentionally emitted as compact strings.

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
    """Render a bounded deterministic index; cluster media is on detail pages."""
    faces = {face.face_id: face for analysis in analyses for face in analysis.faces}
    cards = "\n".join(
        _index_card(cluster, faces, f"people/{cluster.cluster_id}/faces/")
        for cluster in ordered_face_clusters(clusters)
    )
    counts = metrics.get("counts", {})
    summary = "".join(
        f"<tr><th>{escape(str(name))}</th><td>{escape(str(value))}</td></tr>"
        for name, value in sorted(counts.items())
    )
    return _page(
        "Anonymous face clusters",
        "<h1>Anonymous face clusters</h1>"
        f'<table>{summary}</table><main class="clusters">{cards or "<p>No successfully embedded faces.</p>"}</main>',
    )


def render_cluster_detail_pages(
    analyses: Sequence[EventPhotoAnalysis], clusters: Sequence[FaceCluster]
) -> dict[str, str]:
    """Return one self-contained detail page per cluster for a completed run."""
    faces = {face.face_id: face for analysis in analyses for face in analysis.faces}
    return {
        cluster.cluster_id: _detail_page(cluster, faces, "faces/", "photos/", "../../report.html")
        for cluster in ordered_face_clusters(clusters)
    }


def render_review_index(clusters: Sequence[Mapping[str, Any]], *, media_prefix: str) -> str:
    """Render a bounded index for a derived review bundle."""
    cards = "\n".join(
        _review_index_card(cluster, media_prefix) for cluster in sorted(clusters, key=_cluster_key)
    )
    return _page(
        "Anonymous face clusters",
        f'<h1>Anonymous face clusters</h1><main class="clusters">{cards}</main>',
    )


def render_review_detail_page(cluster: Mapping[str, Any], *, media_prefix: str) -> str:
    """Render a derived detail page whose media stays in the immutable run."""
    cluster_id = str(cluster["cluster_id"])
    faces = "".join(
        _image(
            f"{media_prefix}people/{cluster_id}/faces/{quote(str(member['asset']), safe='-._~')}",
            str(member["face_id"]),
        )
        for member in cluster["members"]
    )
    photos = "".join(
        _image(
            f"{media_prefix}people/{cluster_id}/photos/{quote(str(filename), safe='-._~')}",
            str(filename),
        )
        for filename in cluster["filenames"]
    )
    return _page(
        cluster_id,
        f'<p><a href="../../report.html">Cluster index</a></p><h1>{escape(cluster_id)}</h1>'
        f'<h2>Faces</h2><div class="media">{faces}</div>'
        f'<h2>Source photos</h2><div class="media">{photos}</div>',
    )


def _index_card(cluster: FaceCluster, faces: Mapping[str, Any], face_prefix: str) -> str:
    representative = faces.get(cluster.representative_face_id)
    asset = Path(
        representative.crop_path
        if representative is not None
        else face_crop_path(cluster.representative_face_id)
    ).name
    filenames = {
        faces[member.face_id].filename
        if member.face_id in faces
        else member.face_id.partition("#")[0]
        for member in cluster.members
    }
    cluster_id = escape(cluster.cluster_id)
    return (
        f'<section id="{cluster_id}" class="cluster"><a href="people/{cluster_id}/index.html">'
        f"{_image(face_prefix + quote(asset, safe='-._~'), cluster.representative_face_id)}"
        f'</a><h2><a href="people/{cluster_id}/index.html">{cluster_id}</a></h2>'
        f"<p>{len(cluster.members)} faces, {len(filenames)} photos</p></section>"
    )


def _review_index_card(cluster: Mapping[str, Any], media_prefix: str) -> str:
    cluster_id = str(cluster["cluster_id"])
    encoded = quote(cluster_id, safe="-._~")
    representative = str(cluster["representative_asset"])
    source = f"{media_prefix}people/{encoded}/faces/{quote(representative, safe='-._~')}"
    return (
        f'<section id="{escape(cluster_id)}" class="cluster"><a href="people/{encoded}/index.html">'
        f"{_image(source, cluster_id)}"
        f'</a><h2><a href="people/{encoded}/index.html">{escape(cluster_id)}</a></h2>'
        f"<p>{cluster['face_count']} faces, {cluster['photo_count']} photos</p></section>"
    )


def _detail_page(
    cluster: FaceCluster,
    faces: Mapping[str, Any],
    face_prefix: str,
    photo_prefix: str,
    index_href: str,
) -> str:
    face_html = "".join(
        _image(
            face_prefix
            + quote(
                Path(
                    faces[member.face_id].crop_path
                    if member.face_id in faces
                    else face_crop_path(member.face_id)
                ).name,
                safe="-._~",
            ),
            member.face_id,
        )
        for member in sorted(cluster.members, key=lambda item: item.face_id)
    )
    filenames = sorted(
        faces[member.face_id].filename
        if member.face_id in faces
        else member.face_id.partition("#")[0]
        for member in cluster.members
    )
    photo_html = "".join(
        _image(photo_prefix + quote(filename, safe="-._~"), filename)
        for filename in dict.fromkeys(filenames)
    )
    return _page(
        cluster.cluster_id,
        f'<p><a href="{index_href}">Cluster index</a></p><h1>{escape(cluster.cluster_id)}</h1>'
        f'<h2>Faces</h2><div class="media">{face_html}</div>'
        f'<h2>Source photos</h2><div class="media">{photo_html}</div>',
    )


def _image(src: str, alt: str) -> str:
    return f'<figure><img loading="lazy" src="{escape(src, quote=True)}" alt="{escape(alt)}"><figcaption>{escape(alt)}</figcaption></figure>'


def _page(title: str, body: str) -> str:
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">',
            f"<title>{escape(title)}</title>",
            "<style>body{font:16px system-ui;margin:2rem}.clusters{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:1rem}.cluster,figure{margin:0}.media{display:flex;flex-wrap:wrap;gap:1rem}.media figure{max-width:320px}img{display:block;max-width:100%;max-height:260px;object-fit:contain}figcaption{overflow-wrap:anywhere;font-size:.8rem}</style></head><body>",
            body,
            "</body></html>",
            "",
        )
    )


def _cluster_key(cluster: Mapping[str, Any]) -> tuple[int, str]:
    value = str(cluster["cluster_id"])
    return (int(value.partition("-")[2]), value)
