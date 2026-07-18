from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

from botocore.exceptions import ClientError


def client_error(
    status: int, code: str = "Failure", message: str = "secret response"
) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "fake-operation",
    )


class FakeBody:
    def __init__(self, content: bytes) -> None:
        self._stream = BytesIO(content)
        self.closed = False

    def read(self) -> bytes:
        return self._stream.read()

    def close(self) -> None:
        self.closed = True
        self._stream.close()


@dataclass
class FakeObject:
    content: bytes
    etag: str
    content_type: str = "image/jpeg"


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, FakeObject] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failures: dict[str, Exception] = {}
        self.mutate_before: dict[str, tuple[str, FakeObject]] = {}
        self.last_body: FakeBody | None = None
        self.copy_result_etag: object | None = None
        self.presigned_response: dict[str, Any] = {
            "url": "https://upload.example.test/private",
            "fields": {"key": "signed-key", "policy": "signed-policy"},
        }

    def put_object(
        self,
        key: str,
        content: bytes,
        etag: str,
        content_type: str = "image/jpeg",
    ) -> None:
        self.objects[key] = FakeObject(content, etag, content_type)

    def mutate_on(self, operation: str, key: str, replacement: FakeObject) -> None:
        self.mutate_before[operation] = (key, replacement)

    def generate_presigned_post(self, **kwargs: Any) -> dict[str, Any]:
        self._before("generate_presigned_post")
        self.calls.append(("generate_presigned_post", kwargs))
        self._raise("generate_presigned_post")
        return self.presigned_response

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self._before("head_object", kwargs["Key"])
        self.calls.append(("head_object", kwargs))
        self._raise("head_object")
        obj = self._find(kwargs["Key"])
        if_match = kwargs.get("IfMatch")
        if if_match is not None and if_match != obj.etag:
            raise client_error(412, "PreconditionFailed")
        return {
            "ETag": obj.etag,
            "ContentLength": len(obj.content),
            "ContentType": obj.content_type,
        }

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self._before("get_object")
        self.calls.append(("get_object", kwargs))
        self._raise("get_object")
        obj = self._find(kwargs["Key"])
        if kwargs.get("IfMatch") != obj.etag:
            raise client_error(412, "PreconditionFailed")
        start, end = (int(value) for value in kwargs["Range"].removeprefix("bytes=").split("-"))
        self.last_body = FakeBody(obj.content[start : end + 1])
        return {"Body": self.last_body}

    def copy_object(self, **kwargs: Any) -> dict[str, Any]:
        self._before("copy_object")
        self.calls.append(("copy_object", kwargs))
        self._raise("copy_object")
        source = self._find(kwargs["CopySource"]["Key"])
        if kwargs.get("CopySourceIfMatch") != source.etag:
            raise client_error(412, "PreconditionFailed")
        self.objects[kwargs["Key"]] = FakeObject(
            bytes(source.content), source.etag, source.content_type
        )
        result_etag = source.etag if self.copy_result_etag is None else self.copy_result_etag
        return {"CopyObjectResult": {"ETag": result_etag}}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self._before("delete_object")
        self.calls.append(("delete_object", kwargs))
        self._raise("delete_object")
        self.objects.pop(kwargs["Key"], None)
        return {}

    def _before(self, operation: str, requested_key: str | None = None) -> None:
        mutation = self.mutate_before.pop(operation, None)
        if mutation is not None:
            key, replacement = mutation
            if requested_key is not None and requested_key != key:
                self.mutate_before[operation] = mutation
                return
            self.objects[key] = replacement

    def _raise(self, operation: str) -> None:
        failure = self.failures.pop(operation, None)
        if failure is not None:
            raise failure

    def _find(self, key: str) -> FakeObject:
        try:
            return self.objects[key]
        except KeyError:
            raise client_error(404, "NoSuchKey") from None
