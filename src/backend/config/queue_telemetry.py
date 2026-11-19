"""Validate and aggregate the queue completion protocol without database access."""

import json
import re
from collections.abc import Iterator
from threading import Lock
from typing import Any

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

from config.queue_telemetry_protocol import (
    BACKLOG_CHANGED_EVENT,
    COMPLETED_EVENT,
    PHOTO_IMPORT_QUEUES,
    VERSION,
    PhotoImportBacklogs,
)

MAX_PAYLOAD_BYTES = 2048
MAX_QUEUES = 32
MAX_SEGMENTS = 16
MAX_SEGMENT_MS = 2**31 - 1
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")


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
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data["version"] != VERSION
    ):
        return None
    return data


def _parse_completion(data: dict[str, Any]) -> tuple[str, dict[str, int]] | None:
    if data.keys() != {"event", "version", "queue", "segments_ms"}:
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


def _parse_backlog_delta(data: dict[str, Any]) -> PhotoImportBacklogs | None:
    if data.keys() != {"event", "version", "deltas"}:
        return None
    deltas = data["deltas"]
    if not isinstance(deltas, dict) or not deltas or not deltas.keys() <= PHOTO_IMPORT_QUEUES:
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
    return result


class QueueTelemetry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._listening = False
        self._counts: dict[str, int] = {}
        self._durations_ms: dict[str, dict[str, int]] = {}
        self._backlogs: PhotoImportBacklogs = {}
        self._reconciled_at: float | None = None

    def set_listening(self, listening: bool) -> None:
        with self._lock:
            self._listening = listening

    def set_backlog_snapshot(self, counts: PhotoImportBacklogs, *, reconciled_at: float) -> None:
        with self._lock:
            self._backlogs = counts.copy()
            self._reconciled_at = reconciled_at

    def record(self, payload: str) -> None:
        data = _decode(payload)
        if data is None:
            return
        if data.get("event") == BACKLOG_CHANGED_EVENT:
            deltas = _parse_backlog_delta(data)
            if deltas is not None:
                self._record_backlog_delta(deltas)
        elif data.get("event") == COMPLETED_EVENT:
            completion = _parse_completion(data)
            if completion is not None:
                self._record_completion(*completion)

    def _record_backlog_delta(self, deltas: PhotoImportBacklogs) -> None:
        with self._lock:
            for queue, (pending_change, failed_change) in deltas.items():
                if queue in self._backlogs:
                    pending, failed = self._backlogs[queue]
                    self._backlogs[queue] = (pending + pending_change, failed + failed_change)

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
