"""Fixed remote worker transport and pool configuration boundary."""

from __future__ import annotations

import re
from ipaddress import IPv4Address, IPv4Network

REMOTE_API_URL = "https://findme-photo.ru:8443/internal/photo-processing/v1"
POOL_IDENTITIES = {
    "bulk": (
        "1/capture_metadata/2,2/generate_preview/1,2/generate_watermarked_preview/1,"
        "2/face_embedding/3,3/face_embedding/5,1/bib_recognition/1"
    ),
    "selfie": "1/selfie_query/2",
}
_PRIVATE_NETWORKS = tuple(
    IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def validate_private_ipv4(value: str) -> str:
    try:
        address = IPv4Address(value)
    except ValueError:
        raise ValueError("remote API requires explicit private IPv4") from None
    if not any(address in network for network in _PRIVATE_NETWORKS):
        raise ValueError("remote API requires RFC1918 IPv4")
    return str(address)


def validate_remote_config(
    *,
    pool: str,
    private_ip: str,
    build: str,
    image: str,
    identities: str,
    processor_types: str | None,
) -> None:
    validate_private_ipv4(private_ip)
    if pool not in POOL_IDENTITIES:
        raise ValueError("unknown remote worker pool")
    if identities != POOL_IDENTITIES[pool] or processor_types:
        raise ValueError("remote worker requires exact pool identities")
    if re.fullmatch(r"[0-9a-f]{40}", build) is None:
        raise ValueError("remote worker build must be immutable Git SHA")
    if (
        re.fullmatch(
            r"ghcr\.io/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._-]*-worker@sha256:[0-9a-f]{64}", image
        )
        is None
    ):
        raise ValueError("remote worker image must be an immutable GHCR worker digest")
