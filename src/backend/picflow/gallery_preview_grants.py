from __future__ import annotations

from collections.abc import Collection
from typing import Final, Protocol

from picflow.gallery import gallery_preview_final_key
from picflow.models import Photo

GALLERY_PREVIEW_URL_TTL_SECONDS: Final = 21_600


class AcceptedPreviewSigner(Protocol):
    def sign_accepted_preview(self, *, key: str, expires_in: int) -> str: ...


def issue_gallery_preview_urls(
    *, photos: Collection[Photo], signer: AcceptedPreviewSigner
) -> dict[str, str]:
    """Issue direct small-preview URLs for an already authorized gallery page."""
    photo_list = list(photos)
    if len({photo.pk for photo in photo_list}) != len(photo_list):
        raise ValueError("gallery page contains duplicate photo identities")

    urls = {}
    for photo in photo_list:
        key = gallery_preview_final_key(photo)
        if key is not None:
            urls[photo.pk] = signer.sign_accepted_preview(
                key=key,
                expires_in=GALLERY_PREVIEW_URL_TTL_SECONDS,
            )
    return urls
