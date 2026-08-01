from typing import Final

from django.core import signing

CURSOR_VERSION: Final = 1
CURSOR_SALT: Final = "picflow.gallery.cursor"
CURSOR_MAX_AGE_SECONDS: Final = 60 * 60


class InvalidCursor(Exception):
    """Raised when a cursor cannot continue this collection."""


class SignedCursor:
    def encode(self, *, collection: str, last_key: str) -> str:
        return signing.dumps(
            {"v": CURSOR_VERSION, "c": collection, "k": last_key}, salt=CURSOR_SALT, compress=True
        )

    def decode(self, *, cursor: str, collection: str) -> str:
        try:
            payload = signing.loads(cursor, salt=CURSOR_SALT, max_age=CURSOR_MAX_AGE_SECONDS)
        except signing.BadSignature:
            raise InvalidCursor() from None
        if (
            not isinstance(payload, dict)
            or payload.get("v") != CURSOR_VERSION
            or payload.get("c") != collection
            or not isinstance(payload.get("k"), str)
        ):
            raise InvalidCursor()
        return payload["k"]
