import re
from typing import Any

from storages.backends.s3 import S3Storage

_COVER_KEY = re.compile(
    r"event-covers/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"(?:\.[a-z0-9]{1,10})?"
)


def is_event_cover_key(key: str) -> bool:
    return _COVER_KEY.fullmatch(key) is not None


class EventMediaStorage(S3Storage):
    def get_object_parameters(self, name: str) -> dict[str, Any]:
        parameters: dict[str, Any] = super().get_object_parameters(name)
        if is_event_cover_key(name):
            parameters["ACL"] = "public-read"
        else:
            parameters.pop("ACL", None)
        return parameters
