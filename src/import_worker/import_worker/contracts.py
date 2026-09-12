"""Worker v1 limits; authoritative work/retry state belongs to the private API."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    api_url: str
    token: str
    temp_dir: Path = Path("/tmp/photo-import")
    version: ClassVar[int] = 1
    max_bytes: ClassVar[int] = 50 * 1024 * 1024
    json_bytes: ClassVar[int] = 1024 * 1024
    page_size: ClassVar[int] = 100
    max_entries: ClassVar[int] = 10_000
    request_seconds: ClassVar[int] = 45
    heartbeat_seconds: ClassVar[int] = 20
    lease_seconds: ClassVar[int] = 120
    io_seconds: ClassVar[int] = 10

    def __post_init__(self) -> None:
        parsed = urlsplit(self.api_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path != "/internal/photo-import/v1/"
            or not self.token
            or not self.token.isascii()
            or any(ord(char) < 33 or ord(char) == 127 for char in self.token)
        ):
            raise ValueError("Invalid private import API configuration.")
