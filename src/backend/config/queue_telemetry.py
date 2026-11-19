"""Validate and aggregate the queue completion protocol without database access."""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

from config.queue_telemetry_protocol import (
    BACKLOG_CHANGED_EVENT,
    BACKLOG_VERSION,
    COMPLETED_EVENT,
    COMPLETION_VERSION,
    PHOTO_IMPORT_QUEUES,
    PhotoImportBacklogs,
)

MAX_PAYLOAD_BYTES = 2048
MAX_QUEUES = 32
MAX_SEGMENTS = 16
MAX_SEGMENT_MS = 2**31 - 1
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
_PHOTO_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate event key")
    return result


def _decode(payload: str) -> dict[str, Any] | None:
    if not payload.isascii() or len(payload) > MAX_PAYLOAD_BYTES:
        return None
    try:
        data = json.loads(payload, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or type(data.get("version")) is not int:
        return None
    return data


def _parse_completion(data: dict[str, Any]) -> tuple[str, dict[str, int]] | None:
    if data.keys() != {"event", "version", "queue", "segments_ms"} or (
        data["version"] != COMPLETION_VERSION
    ):
        return None
    queue, segments = data["queue"], data["segments_ms"]
    if (
        not isinstance(queue, str)
        or not _NAME.fullmatch(queue)
        or not isinstance(segments, dict)
        or not 1 <= len(segments) <= MAX_SEGMENTS
    ):
        return None
    if any(
        not _NAME.fullmatch(name) or type(value) is not int or not 0 <= value <= MAX_SEGMENT_MS
        for name, value in segments.items()
    ):
        return None
    return queue, segments


def _parse_backlog_delta(data: dict[str, Any]) -> tuple[str, PhotoImportBacklogs] | None:
    if (
        data.keys() != {"event", "version", "photo_id", "deltas"}
        or data["version"] != BACKLOG_VERSION
    ):
        return None
    photo_id, deltas = data["photo_id"], data["deltas"]
    if (
        not isinstance(photo_id, str)
        or not _PHOTO_ID.fullmatch(photo_id)
        or not isinstance(deltas, dict)
        or not deltas
        or not deltas.keys() <= PHOTO_IMPORT_QUEUES
    ):
        return None
    result = {}
    for queue, pair in deltas.items():
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(type(value) is not int or not -1 <= value <= 1 for value in pair)
        ):
            return None
        result[queue] = (pair[0], pair[1])
    return photo_id, result


def _empty_backlogs() -> PhotoImportBacklogs:
    return dict.fromkeys(PHOTO_IMPORT_QUEUES, (0, 0))


def _add_backlogs(totals: PhotoImportBacklogs, changes: PhotoImportBacklogs) -> None:
    for queue, (pending_change, failed_change) in changes.items():
        pending, failed = totals[queue]
        totals[queue] = (pending + pending_change, failed + failed_change)


@dataclass(slots=True)
class _BacklogReconciliation:
    totals: PhotoImportBacklogs = field(default_factory=_empty_backlogs)
    upper_bound: str | None = None
    after: str | None = None
    upper_bound_loaded: bool = False

    def misses_photo(self, photo_id: str) -> bool:
        # PostgreSQL pattern-ops keyset order matches this comparison.
        return self.upper_bound_loaded and (
            self.upper_bound is None
            or photo_id > self.upper_bound
            or (self.after is not None and photo_id <= self.after)
        )


class QueueTelemetry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._listening = False
        self._counts: dict[str, int] = {}
        self._durations_ms: dict[str, dict[str, int]] = {}
        self._backlogs = _empty_backlogs()
        self._reconciliation: _BacklogReconciliation | None = None
        self._reconciled_at: float | None = None

    def set_listening(self, listening: bool) -> None:
        with self._lock:
            self._listening = listening
            if not listening:
                self._reconciliation = None

    def start_backlog_reconciliation(self) -> _BacklogReconciliation | None:
        with self._lock:
            if not self._listening or self._reconciliation is not None:
                return None
            self._reconciliation = _BacklogReconciliation()
            return self._reconciliation

    def set_backlog_reconciliation_upper_bound(
        self, reconciliation: _BacklogReconciliation, upper_bound: str | None
    ) -> bool:
        with self._lock:
            if self._reconciliation is not reconciliation:
                return False
            reconciliation.upper_bound = upper_bound
            reconciliation.upper_bound_loaded = True
            return True

    def add_backlog_reconciliation_page(
        self, reconciliation: _BacklogReconciliation, *, after: str, counts: PhotoImportBacklogs
    ) -> bool:
        with self._lock:
            if self._reconciliation is not reconciliation:
                return False
            _add_backlogs(reconciliation.totals, counts)
            reconciliation.after = after
            return True

    def finish_backlog_reconciliation(
        self, reconciliation: _BacklogReconciliation, *, reconciled_at: float
    ) -> None:
        with self._lock:
            if self._reconciliation is reconciliation:
                self._backlogs = reconciliation.totals
                self._reconciled_at = reconciled_at
                self._reconciliation = None

    def discard_backlog_reconciliation(self, reconciliation: _BacklogReconciliation) -> None:
        with self._lock:
            if self._reconciliation is reconciliation:
                self._reconciliation = None

    def record(self, payload: str) -> None:
        data = _decode(payload)
        if data is None:
            return
        if data.get("event") == BACKLOG_CHANGED_EVENT:
            delta = _parse_backlog_delta(data)
            if delta is not None:
                self._record_backlog_delta(*delta)
        elif data.get("event") == COMPLETED_EVENT:
            completion = _parse_completion(data)
            if completion is not None:
                self._record_completion(*completion)

    def _record_backlog_delta(self, photo_id: str, deltas: PhotoImportBacklogs) -> None:
        with self._lock:
            _add_backlogs(self._backlogs, deltas)
            reconciliation = self._reconciliation
            if reconciliation is not None and reconciliation.misses_photo(photo_id):
                _add_backlogs(reconciliation.totals, deltas)

    def _record_completion(self, queue: str, segments_ms: dict[str, int]) -> None:
        with self._lock:
            if queue not in self._counts:
                if len(self._counts) >= MAX_QUEUES:
                    return
                self._counts[queue] = 1
                self._durations_ms[queue] = segments_ms
                return
            totals = self._durations_ms[queue]
            if totals.keys() != segments_ms.keys():
                return
            self._counts[queue] += 1
            for name, duration in segments_ms.items():
                totals[name] += duration

    def collect(self) -> Iterator[Metric]:
        with self._lock:
            listening = self._listening
            counts = self._counts.copy()
            backlogs = self._backlogs.copy()
            reconciled_at = self._reconciled_at
            durations_ms = {queue: totals.copy() for queue, totals in self._durations_ms.items()}

        completed = CounterMetricFamily(
            "findme_queue_completed", "Observed successful queue completions.", labels=["queue"]
        )
        duration = CounterMetricFamily(
            "findme_queue_segment_duration_seconds",
            "Elapsed segment time across observed successful queue completions.",
            labels=["queue", "segment"],
        )
        for queue, count in counts.items():
            completed.add_metric([queue], count)
            for segment, total in durations_ms[queue].items():
                duration.add_metric([queue, segment], total / 1000)

        yield GaugeMetricFamily(
            "findme_queue_telemetry_listening",
            "Whether the queue telemetry collector is subscribed to PostgreSQL.",
            value=int(listening),
        )
        yield completed
        yield duration
        if reconciled_at is not None:
            pending_gauge = GaugeMetricFamily(
                "findme_photo_import_pending_count",
                "Approximate count of photos with an unfinished import path.",
                labels=["queue"],
            )
            failed_gauge = GaugeMetricFamily(
                "findme_photo_import_failed_count",
                "Approximate count of photos blocked by a permanent processing failure.",
                labels=["queue"],
            )
            for queue, (pending, failed) in backlogs.items():
                pending = max(0, pending)
                pending_gauge.add_metric([queue], pending)
                failed_gauge.add_metric([queue], min(pending, max(0, failed)))
            yield pending_gauge
            yield failed_gauge
            yield GaugeMetricFamily(
                "findme_photo_import_last_reconciliation_timestamp_seconds",
                "Unix time of the last successful photo import backlog reconciliation.",
                value=reconciled_at,
            )
