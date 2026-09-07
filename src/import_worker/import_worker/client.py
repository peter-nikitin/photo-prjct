"""The sole holder of the private API bearer credential."""

import json
import random
from collections.abc import Callable
from typing import Any
from uuid import UUID

from import_worker.contracts import Config
from import_worker.transport import Transport, TransportError


class CallbackUnavailable(Exception):
    """Bounded callback transport replay exhausted; do not report a source failure."""


class APIClient:
    def __init__(self, config: Config, transport: Transport):
        self.config = config
        self.transport = transport

    def call(self, endpoint: str, **payload: object) -> dict[str, Any]:
        allowed = {"claim", "readiness"}
        if endpoint not in allowed:
            parts = endpoint.split("/")
            if len(parts) < 3 or parts[0] != "attempts":
                raise ValueError("Invalid API operation.")
            UUID(parts[1])
            allowed |= {
                f"attempts/{parts[1]}/{suffix}"
                for suffix in (
                    "renew",
                    "manifest/pages",
                    "manifest/finalize",
                    "prepare-upload",
                    "complete",
                    "fail",
                )
            }
        if endpoint not in allowed:
            raise ValueError("Invalid API operation.")
        body = json.dumps(
            dict(contract_version=Config.version, **payload),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
        if len(body) > Config.json_bytes:
            raise TransportError()
        result = self.transport.json(
            "POST",
            self.config.api_url + endpoint,
            audience="api",
            headers={
                "Authorization": "Bearer " + self.config.token,
                "Content-Type": "application/json",
            },
            body=body,
        )
        if (
            type(result.get("contract_version")) is not int
            or result["contract_version"] != Config.version
        ):
            raise TransportError()
        return result

    def callback(
        self,
        endpoint: str,
        *,
        check: Callable[[], None],
        sleep: Callable[[float], None],
        **payload: object,
    ) -> dict[str, Any]:
        if endpoint == "claim" or endpoint.endswith("/renew"):
            raise ValueError("Claims and lease renewal are not replayed callbacks.")
        for attempt in range(4):
            check()
            try:
                return self.call(endpoint, **payload)
            except TransportError as error:
                if not error.retryable:
                    raise
                if attempt == 3:
                    raise CallbackUnavailable() from None
                delay = max(2**attempt + random.uniform(0, 1), error.retry_after)
                while delay > 0:
                    check()
                    interval = min(delay, Config.heartbeat_seconds)
                    sleep(interval)
                    delay -= interval
        raise AssertionError("Unreachable callback retry state.")
