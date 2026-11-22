"""Calendar-time accounting for the serial batch lease owner."""

from datetime import datetime

from config.queue_timings import elapsed_ms

from ingestion.models import ImportAttempt, ImportBatch

SEGMENTS = ("wait", "manifest", "download", "upload", "publish", "idle", "retry")


def _offset(batch: ImportBatch, at: datetime) -> int | None:
    enqueued = batch.queue_enqueued_at
    if enqueued is None or at < enqueued:
        return None
    return elapsed_ms(enqueued, at)


def claim(batch: ImportBatch, now: datetime) -> None:
    offset = _offset(batch, now)
    timings = batch.queue_timings
    if offset is None or timings is None:
        return
    if not timings:
        timings.update(dict.fromkeys(SEGMENTS, 0), wait=offset)
    else:
        # A still-open timing interval belongs to an expired lease. Claim already
        # established that a previous lease cannot still own this batch.
        interrupted = timings.get("claim")
        previous = interrupted if interrupted is not None else timings["last"]
        gap = offset - previous
        if gap < 0:
            batch.queue_timings = None
        else:
            retrying = interrupted is not None or timings["retrying"]
            timings["retry" if retrying else "idle"] += gap
    timings["claim"] = offset
    batch.save(update_fields=["queue_timings"])


def finish(
    batch: ImportBatch,
    attempt: ImportAttempt,
    now: datetime,
    *,
    succeeded: bool,
    duplicate: bool = False,
) -> None:
    timings = batch.queue_timings
    offset = _offset(batch, now)
    started = _offset(batch, attempt.claimed_at)
    if not timings or offset is None or started is None:
        return
    if timings.pop("claim", None) != started or offset < started:
        batch.queue_timings = None
    elif not succeeded:
        timings["retry"] += offset - started
    elif attempt.kind == ImportAttempt.Kind.MANIFEST:
        timings["manifest"] += offset - started
    elif duplicate:
        timings["download"] += offset - started
    else:
        upload = attempt.queue_upload_started_at
        publish = attempt.queue_publish_started_at
        if upload is None or publish is None or not attempt.claimed_at <= upload <= publish <= now:
            batch.queue_timings = None
        else:
            sent_at = _offset(batch, upload)
            published_at = _offset(batch, publish)
            assert sent_at is not None and published_at is not None
            timings["download"] += sent_at - started
            timings["upload"] += published_at - sent_at
            timings["publish"] += offset - published_at
    timings["last"] = offset
    timings["retrying"] = not succeeded
    batch.save(update_fields=["queue_timings"])


def paused(batch: ImportBatch, now: datetime) -> None:
    timings = batch.queue_timings
    offset = _offset(batch, now)
    if timings is None or offset is None or "claim" in timings:
        return
    if not timings:
        timings.update(dict.fromkeys(SEGMENTS, 0), wait=offset)
    elif offset >= timings["last"] and not timings["retrying"]:
        timings["idle"] += offset - timings["last"]
    else:
        return
    timings["last"] = offset
    timings["retrying"] = True
    batch.save(update_fields=["queue_timings"])


def completed(batch: ImportBatch, now: datetime) -> dict[str, int] | None:
    timings = batch.queue_timings
    offset = _offset(batch, now)
    if not timings or offset is None or offset < timings.get("last", offset + 1):
        return None
    values = {segment: timings[segment] for segment in SEGMENTS}
    values["retry" if timings["retrying"] else "idle"] += offset - timings["last"]
    return values
