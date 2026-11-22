import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

from findme_client.api import Api, ClientError, Json, backoff, retryable, send_original
from findme_client.config import api_for
from findme_client.tasks import Original, Task, scan

MISSING_OBJECT = {"object_missing", "object_changed"}
MAX_ATTEMPTS = 4


def prepare(profile: str, event: int, folder: int | None, source: Path) -> Task:
    with api_for(profile) as api:
        identity = api.request("whoami/")
        limits = identity["upload"]
        if not limits["enabled"]:
            raise ClientError("upload_disabled", "This account cannot upload photos.")
        api.request(f"events/{event}/")
        if folder is not None and not any(
            row["id"] == folder for row in pages(api, f"events/{event}/folders/")
        ):
            raise ClientError("invalid_folder", "The folder is unavailable for this event.")
    files = scan(source).files
    if not 1 <= len(files) <= limits["max_files"]:
        raise ClientError("invalid_file_count", "The number of JPEGs is out of range.")
    return Task.create(
        profile=profile,
        server=api.server,
        owner=identity["user"]["id"],
        event=event,
        folder=folder,
        originals=[Original.capture(path, limits["max_file_bytes"]) for path in files],
    )


@contextmanager
def authorized_api(metadata: sqlite3.Row) -> Iterator[tuple[Api, Json]]:
    with api_for(metadata["profile"]) as api:
        if api.server != metadata["server"]:
            raise ClientError("profile_changed", "Restore the original server for this profile.")
        identity = api.request("whoami/")
        if identity["user"]["id"] != metadata["owner"]:
            raise ClientError("profile_changed", "Sign in as the task's original owner.")
        yield api, identity


def status(task: Task) -> Json:
    result = task.summary()
    batch = result["batch_id"]
    if batch:
        try:
            with authorized_api(task.metadata()) as (api, _):
                result["server"] = api.request(f"batches/{batch}/")
        except ClientError as exc:
            result["server_error"] = exc.payload()["error"]
    return result


def pages(api: Api, path: str) -> Iterator[Json]:
    page: int | None = 1
    while page is not None:
        response = api.request(path, page=page)
        yield from response["results"]
        page = response["next_page"]


def worker(task: Task, fd: int) -> int:
    try:
        # The parent's inherited file description owns the lock for this process.
        lock = os.fstat(fd)
        expected = (task.directory / "lock").stat()
        if (lock.st_dev, lock.st_ino) != (expected.st_dev, expected.st_ino):
            return 2
        run(task)
    except ClientError as exc:
        task.record_state("interrupted", exc.code)
        return 1
    except Exception:
        task.record_state("interrupted", "worker_error")
        return 1
    finally:
        os.close(fd)
    return 0


def run(task: Task) -> None:
    metadata = task.metadata()
    with authorized_api(metadata) as (api, identity), httpx.Client() as storage:
        upload(task, metadata, api, identity["upload"], storage)


def upload(
    task: Task,
    metadata: sqlite3.Row,
    api: Api,
    limits: Json,
    storage: httpx.Client,
) -> None:
    if not limits["enabled"]:
        raise ClientError("upload_disabled", "This account cannot upload photos.")
    if stop_requested(task):
        return
    files = task.files()
    batch = metadata["batch"]
    if batch is None:
        result = api.request(
            "batches/",
            data={
                "client_task_id": task.id,
                "event_id": metadata["event"],
                "expected_item_count": len(files),
            },
        )
        batch = result["batch"]["id"]
        task.record_batch(batch)

    directory = f"batches/{batch}/"
    for page in page_items(api, directory + "items/"):
        task.record_items(page)
    missing = [original(row) for row in task.files() if row["item"] is None]
    chunk = limits["registration_chunk"]
    for index in range(0, len(missing), chunk):
        if stop_requested(task):
            return
        response = api.request(
            directory + "items/",
            data={
                "items": [
                    entry.registration(metadata["folder"])
                    for entry in missing[index : index + chunk]
                ]
            },
        )
        task.record_items(response["items"])

    for row in task.files():
        if stop_requested(task):
            return
        if row["state"] == "confirmed":
            continue
        entry = original(row)
        endpoint = f"{directory}items/{row['item']}/"
        try:
            if not transfer(task, api, storage, endpoint, entry):
                return
        except ClientError as exc:
            if exc.code in {"file_changed", "transfer_retries_exhausted"}:
                server = api.request(endpoint)["item"]
                if server["status"] == "uploaded":
                    task.record_file(entry.id, "confirmed")
                    continue
                if server["status"] != "failed":
                    api.request(endpoint + "failed/", data={"code": "transfer_retries_exhausted"})
                task.record_file(entry.id, "failed", exc.code)
            elif exc.code in {
                "size_mismatch",
                "content_type_mismatch",
                "invalid_jpeg",
                "promotion_conflict",
                "internal_error",
            }:
                if api.request(endpoint)["item"]["status"] != "failed":
                    raise
                task.record_file(entry.id, "failed", exc.code)
            else:
                raise
        else:
            task.record_file(entry.id, "confirmed")

    final = api.request(directory + "finalize/", data={})
    task.record_state(final["batch"]["status"])


def page_items(api: Api, path: str) -> Iterator[list[Json]]:
    page: int | None = 1
    while page is not None:
        response = api.request(path, page=page)
        yield response["results"]
        page = response["next_page"]


def original(row: sqlite3.Row) -> Original:
    return Original(row["id"], Path(row["path"]), row["size"], row["mtime_ns"], row["sha256"])


def stop_requested(task: Task) -> bool:
    if task.stopped:
        task.record_state("stopped")
        return True
    return False


def confirm(api: Api, endpoint: str) -> bool:
    try:
        result = api.request(endpoint + "confirm/", data={}, attempts=1)
    except ClientError as exc:
        if not retryable(exc):
            raise
        current = api.request(endpoint)["item"]
        if current["status"] == "uploaded":
            return True
        if exc.code in MISSING_OBJECT:
            return False
        # Confirm is idempotent even if its response was lost. Check before any retransmit.
        try:
            result = api.request(endpoint + "confirm/", data={})
        except ClientError as second:
            if second.code in MISSING_OBJECT:
                return False
            if api.request(endpoint)["item"]["status"] == "uploaded":
                return True
            raise
    return result["item"]["status"] == "uploaded"


def transfer(
    task: Task,
    api: Api,
    storage: httpx.Client,
    endpoint: str,
    entry: Original,
) -> bool:
    for attempt in range(MAX_ATTEMPTS):
        if stop_requested(task):
            return False
        item = api.request(endpoint)["item"]
        if item["status"] == "uploaded":
            return True
        if item["status"] == "authorized" and confirm(api, endpoint):
            return True
        with entry.verified() as snapshot:
            grant = authorize(api, endpoint, item)
            try:
                send_original(storage, grant, entry.path.name, snapshot)
            except ClientError as exc:
                if not retryable(exc) and exc.status not in {400, 403}:
                    raise
        if confirm(api, endpoint):
            return True
        if attempt + 1 < MAX_ATTEMPTS:
            backoff(attempt)
    raise ClientError("transfer_retries_exhausted", "No upload attempts remain for this JPEG.")


def authorize(api: Api, endpoint: str, item: Json) -> Json:
    should_retry = item["status"] == "failed"
    action = "retry/" if should_retry else "authorize/"
    data = {} if should_retry else {"reason": "data_attempt"}
    for attempt in range(3):
        try:
            return api.request(endpoint + action, data=data, attempts=1)["grant"]
        except ClientError as exc:
            if not retryable(exc):
                raise
            current = api.request(endpoint)["item"]
            if current["status"] == "authorized" and (
                should_retry or current["upload_attempts"] > item["upload_attempts"]
            ):
                response = api.request(endpoint + "authorize/", data={"reason": "grant_refresh"})
                return response["grant"]
            if attempt == 2:
                raise
            backoff(attempt)
    raise AssertionError("At least one attempt is required")
