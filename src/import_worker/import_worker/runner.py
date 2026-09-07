"""Serial leased importer; source operation retry budgets remain entirely server-owned."""

from __future__ import annotations

import fcntl
import hashlib
import json
import random
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

from import_worker.client import APIClient, CallbackUnavailable
from import_worker.contracts import Config
from import_worker.source import DiskSource, SourceError, validate_jpeg
from import_worker.transport import Transport, TransportError


class LeaseLost(Exception):
    pass


class Lease:
    def __init__(
        self, client: APIClient, attempt: str, *, clock: Callable[[], float] = time.monotonic
    ):
        self.client = client
        self.attempt = str(UUID(attempt))
        self.clock = clock
        self.next_renewal = 0.0
        self.deadline = 0.0

    def guard(self) -> None:
        if self.deadline and self.clock() >= self.deadline:
            raise LeaseLost()

    def check(self, *, force: bool = False) -> None:
        now = self.clock()
        if self.deadline and now >= self.deadline:
            raise LeaseLost()
        if force or now >= self.next_renewal:
            try:
                self.client.call(
                    f"attempts/{self.attempt}/renew", lease_seconds=Config.lease_seconds
                )
            except TransportError:
                raise LeaseLost() from None
            # Count from request start, never extend local authority by network response delay.
            self.deadline = now + Config.lease_seconds - Config.io_seconds
            self.next_renewal = now + Config.heartbeat_seconds
            if self.clock() >= self.deadline:
                raise LeaseLost()


class Runner:
    def __init__(
        self,
        config: Config,
        client: APIClient,
        source: DiskSource,
        transport: Transport,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config, self.client, self.source, self.transport = config, client, source, transport
        self.sleep = sleep
        config.temp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (config.temp_dir / "worker.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError("Import temporary directory is already in use.") from None
        self.path = config.temp_dir / "current.jpg"
        self.path.unlink(missing_ok=True)

    def close(self) -> None:
        self.path.unlink(missing_ok=True)
        self.lock.close()

    def run_once(self) -> bool:
        result = self.client.call("claim", lease_seconds=Config.lease_seconds)
        work = result["work"]
        if work is None:
            return False
        lease = Lease(self.client, work["attempt_id"])
        operation = "manifest" if work["kind"] == "manifest" else "download"
        try:
            lease.check(force=True)
            if operation == "manifest":
                self._manifest(work, lease)
            else:
                metadata = work["source"]
                url = self.source.download_url(
                    metadata["key"], metadata["path"], heartbeat=lease.check
                )
                with self.path.open("xb") as stream:
                    self.transport.request(
                        "GET",
                        url,
                        audience="source",
                        limit=Config.max_bytes,
                        sink=stream.write,
                        heartbeat=lease.check,
                    )
                verified = validate_jpeg(self.path, metadata)
                lease.check(force=True)
                operation = "upload"
                prepared = self._callback(
                    lease,
                    "prepare-upload",
                    item_id=work["item_id"],
                    content_sha256=verified.sha256,
                    byte_size=verified.size,
                    oriented_geometry=dict(width=verified.geometry[0], height=verified.geometry[1]),
                )["upload"]
                if prepared["status"] == "duplicate":
                    return True
                grant = prepared["grant"]
                lease.check(force=True)
                self.transport.upload(
                    grant["url"], grant["fields"], self.path, heartbeat=lease.check
                )
                operation = "publication"
                lease.check(force=True)
                self._callback(lease, "complete", item_id=work["item_id"])
        except (LeaseLost, CallbackUnavailable):
            pass
        except (SourceError, TransportError) as error:
            retryable = isinstance(error, TransportError) and error.retryable
            code = (
                error.code
                or {
                    "manifest": "source_unavailable",
                    "download": "download_unavailable",
                    "upload": "upload_unavailable",
                    "publication": "publication_unavailable",
                }[operation]
            )
            try:
                lease.check(force=True)
                self._callback(
                    lease,
                    "fail",
                    operation=operation,
                    code=code,
                    retryable=retryable,
                )
            except (TransportError, LeaseLost, CallbackUnavailable):
                pass
            if retryable and isinstance(error, TransportError):
                # One source attempt per server claim. No local source retry counter.
                self.sleep(max(2 + random.uniform(0, 3), error.retry_after))
        finally:
            self.path.unlink(missing_ok=True)
        return True

    def _manifest(self, work: dict[str, Any], lease: Lease) -> None:
        canonical = ""
        page = 0
        for page_key, entries in self.source.pages(work["source"]["key"], heartbeat=lease.check):
            canonical = page_key
            # Source JSON may be UTF-8; private callbacks escape Unicode. Split on
            # the entire wire envelope, retaining stable page numbers for replays.
            chunks: list[list[dict[str, Any]]] = [[]]
            for entry in entries:
                candidate = chunks[-1] + [entry]
                envelope = dict(
                    contract_version=Config.version,
                    batch_id=work["batch_id"],
                    page_number=page + len(chunks) - 1,
                    page_fingerprint="0" * 64,
                    entries=candidate,
                )
                if (
                    len(json.dumps(envelope, ensure_ascii=True, separators=(",", ":")).encode())
                    > Config.json_bytes
                    or len(candidate) > Config.page_size
                ):
                    if not chunks[-1]:
                        raise SourceError("manifest_too_large")
                    chunks.append([entry])
                else:
                    chunks[-1] = candidate
            for chunk in chunks:
                lease.check()
                fingerprint = hashlib.sha256(
                    json.dumps(chunk, sort_keys=True, ensure_ascii=True).encode()
                ).hexdigest()
                self._callback(
                    lease,
                    "manifest/pages",
                    batch_id=work["batch_id"],
                    page_number=page,
                    page_fingerprint=fingerprint,
                    entries=chunk,
                )
                page += 1
        lease.check(force=True)
        self._callback(
            lease,
            "manifest/finalize",
            batch_id=work["batch_id"],
            canonical_source_key=canonical,
        )

    def _callback(self, lease: Lease, suffix: str, **payload: object) -> dict[str, Any]:
        # Terminal callbacks may already have committed: renewing them would reject a valid replay.
        check = lease.guard if suffix in {"complete", "manifest/finalize", "fail"} else lease.check
        return self.client.callback(
            f"attempts/{lease.attempt}/{suffix}", check=check, sleep=self.sleep, **payload
        )

    def run(self) -> None:
        try:
            while True:
                try:
                    if not self.run_once():
                        self.sleep(5)
                except TransportError:
                    self.sleep(5)
        finally:
            self.close()
