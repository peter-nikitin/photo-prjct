"""Public validators for canonical processing-result scalar values."""

from __future__ import annotations

import re
from datetime import datetime

_CANONICAL_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")


def parse_canonical_timestamp(value: object) -> datetime | None:
    """Return an aware UTC timestamp only for the worker's canonical JSON representation."""
    if not isinstance(value, str) or _CANONICAL_TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
