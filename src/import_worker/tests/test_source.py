import hashlib
import io
from pathlib import Path

import pytest
from import_worker.source import DiskSource, SourceError, validate_jpeg
from PIL import Image


def jpeg_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (12, 8), "red").save(stream, "JPEG")
    return stream.getvalue()


class FixtureTransport:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.urls = []

    def json(self, method, url, **kwargs):
        self.urls.append(url)
        return next(self.payloads)


def test_manifest_paginates_direct_children_preserves_unicode_and_missing_hash():
    pages = [
        dict(
            type="dir",
            public_key="canonical",
            _embedded=dict(
                offset=0,
                total=3,
                items=[
                    dict(type="file", path="/фото.jpg", name="фото.jpg", size=600),
                    dict(type="dir", path="/nested", name="nested"),
                ],
            ),
        ),
        dict(
            type="dir",
            public_key="canonical",
            _embedded=dict(
                offset=2,
                total=3,
                items=[dict(type="file", path="/readme.txt", name="readme.txt", size=0)],
            ),
        ),
    ]
    transport = FixtureTransport(pages)
    results = list(DiskSource(transport).pages("share_token-1"))
    assert [entry["kind"] for _, entries in results for entry in entries] == [
        "jpeg",
        "directory",
        "unsupported",
    ]
    assert results[0][1][0]["sha256"] is None
    assert results[0][1][0]["name"] == "фото.jpg"
    assert "offset=2" in transport.urls[1]
    assert all("sort=name" in url for url in transport.urls)


def test_download_link_requires_get_and_no_template():
    source = DiskSource(
        FixtureTransport(
            [dict(href="https://downloader.disk.yandex.ru/a", method="POST", templated=False)]
        )
    )
    with pytest.raises(SourceError):
        source.download_url("key", "/one.jpg")


@pytest.mark.parametrize("change", ["bytes", "size", "sha256", "md5", "invalid"])
def test_jpeg_validation_rejects_changed_or_invalid_bytes(tmp_path, change):
    data = jpeg_bytes()
    metadata = dict(
        size=len(data), sha256=hashlib.sha256(data).hexdigest(), md5=hashlib.md5(data).hexdigest()
    )
    if change == "bytes":
        data += b"changed"
    elif change == "invalid":
        data = b"not jpeg"
        metadata = dict(size=len(data), sha256="", md5="")
    else:
        metadata[change] = 2 if change == "size" else "0" * (64 if change == "sha256" else 32)
    path = tmp_path / "current.jpg"
    path.write_bytes(data)
    with pytest.raises(SourceError):
        validate_jpeg(path, metadata)


def test_valid_jpeg_computes_hash_when_source_has_none(tmp_path: Path):
    data = jpeg_bytes()
    path = tmp_path / "current.jpg"
    path.write_bytes(data)
    result = validate_jpeg(path, dict(size=len(data), sha256="", md5=""))
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert result.geometry == (12, 8)
