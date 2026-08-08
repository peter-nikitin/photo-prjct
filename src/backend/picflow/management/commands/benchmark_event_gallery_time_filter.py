import json
from collections.abc import Iterable
from datetime import datetime, time
from time import perf_counter
from zoneinfo import ZoneInfo

from config.views import event_detail
from django.contrib.auth.models import AnonymousUser
from django.core.management.base import BaseCommand, CommandError
from django.db.models import QuerySet
from django.test import RequestFactory
from django.urls import reverse
from picflow import capture_time_projection
from picflow.forms import EventGalleryTimeFilterForm
from picflow.gallery import gallery_page
from picflow.models import Event, Photo
from processing.management.commands.report_event_capture_times import _build_report
from processing.management.commands.reprocess_event_capture_times import EXPECTED_EVENT_ID

_REPRESENTATIVE_PAGES = ("1", "mid", "last")
_MAX_RATIO = 2


class Command(BaseCommand):
    help = "Benchmark the read-only event-9 gallery time filter against its unfiltered baseline."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--event-id", required=True, type=int)
        parser.add_argument("--pages", default="1,mid,last")

    def handle(self, *args, **options) -> None:
        if options["event_id"] != EXPECTED_EVENT_ID:
            raise CommandError("this command only permits event ID 9")
        projection_report = capture_time_projection.report_events(event_id=None)
        if not projection_report.get("clean"):
            raise CommandError("global projection reconciliation is not clean")
        event = self._accepted_event()
        page_tokens = self._page_tokens(options["pages"])
        filter_data = self._full_event_filter_data(event)
        filter_form = EventGalleryTimeFilterForm(event, filter_data)
        if not filter_form.is_valid() or filter_form.utc_bounds is None:
            raise CommandError("event 9 cannot construct a valid full-event time filter")
        capture_time_start, capture_time_end = filter_form.utc_bounds

        filtered_first_page = gallery_page(
            event=event,
            page_number="1",
            capture_time_start=capture_time_start,
            capture_time_end=capture_time_end,
        )
        if filtered_first_page.paginator.num_pages == 0:
            raise CommandError("event 9 has no eligible gallery pages to benchmark")

        page_numbers = self._page_numbers(
            tokens=page_tokens, total_pages=filtered_first_page.paginator.num_pages
        )
        pages = [
            self._measure_comparison(
                event=event,
                filter_data=filter_data,
                capture_time_start=capture_time_start,
                capture_time_end=capture_time_end,
                label=label,
                page_number=page_number,
            )
            for label, page_number in page_numbers
        ]
        gate = "passed" if all(page["gate"] == "passed" for page in pages) else "failed"
        report = {
            "corpus": self._corpus_counts(event),
            "event_id": EXPECTED_EVENT_ID,
            "gate": gate,
            "pages": pages,
        }
        self.stdout.write(
            json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        if gate != "passed":
            raise CommandError("performance gate failed")

    def _accepted_event(self) -> Event:
        try:
            event = Event.objects.get(pk=EXPECTED_EVENT_ID)
        except Event.DoesNotExist as error:
            raise CommandError("approved event 9 does not exist") from error
        report = _build_report(event)
        if report["status"] != "accepted":
            raise CommandError("event 9 does not meet the current-v2 capture-time precondition")
        return event

    @staticmethod
    def _corpus_counts(event: Event) -> dict[str, int]:
        report = _build_report(event)
        counts = report["counts"]
        if not isinstance(counts, dict):
            raise CommandError("capture-time report has invalid corpus counts")
        accepted_count = counts.get("accepted_non_null_capture_times")
        event_photo_count = counts.get("event_photo_count")
        if (
            not isinstance(accepted_count, int)
            or isinstance(accepted_count, bool)
            or not isinstance(event_photo_count, int)
            or isinstance(event_photo_count, bool)
        ):
            raise CommandError("capture-time report has invalid corpus counts")
        return {
            "accepted_current_v2_capture_times": accepted_count,
            "event_photo_count": event_photo_count,
        }

    @staticmethod
    def _page_tokens(value: str) -> tuple[str, ...]:
        tokens = tuple(token.strip() for token in value.split(",") if token.strip())
        if not tokens or any(token not in _REPRESENTATIVE_PAGES for token in tokens):
            raise CommandError("--pages accepts only 1,mid,last")
        if len(set(tokens)) != len(tokens):
            raise CommandError("--pages values must be unique")
        return tokens

    @staticmethod
    def _full_event_filter_data(event: Event) -> dict[str, str]:
        first_local = datetime.combine(event.start_date, time.min).replace(
            tzinfo=ZoneInfo(event.timezone_name)
        )
        return {"from": first_local.strftime("%Y-%m-%dT%H:%M")}

    @staticmethod
    def _page_numbers(*, tokens: Iterable[str], total_pages: int) -> tuple[tuple[str, int], ...]:
        positions = {
            "1": ("first", 1),
            "mid": ("midpoint", (total_pages + 1) // 2),
            "last": ("last", total_pages),
        }
        return tuple(positions[token] for token in tokens)

    def _measure_comparison(
        self,
        *,
        event: Event,
        filter_data: dict[str, str],
        capture_time_start: datetime,
        capture_time_end: datetime,
        label: str,
        page_number: int,
    ) -> dict[str, object]:
        unfiltered = self._measure_page(
            event=event,
            page_number=page_number,
            filter_data={},
            capture_time_start=None,
            capture_time_end=None,
        )
        filtered = self._measure_page(
            event=event,
            page_number=page_number,
            filter_data=filter_data,
            capture_time_start=capture_time_start,
            capture_time_end=capture_time_end,
        )
        raw_ratios = {
            "database_execution": self._ratio(
                baseline=unfiltered["database_execution_ms"],
                measured=filtered["database_execution_ms"],
            ),
            "rendered_response": self._ratio(
                baseline=unfiltered["rendered_response_ms"],
                measured=filtered["rendered_response_ms"],
            ),
        }
        ratios = {name: self._reported_ratio(ratio) for name, ratio in raw_ratios.items()}
        passed = all(ratio is not None and ratio <= _MAX_RATIO for ratio in raw_ratios.values())
        gate = "passed" if passed else "failed"
        return {
            "filtered": self._reported_measurement(filtered),
            "gate": gate,
            "page": label,
            "page_number": page_number,
            "ratios": ratios,
            "unfiltered": self._reported_measurement(unfiltered),
        }

    @staticmethod
    def _reported_measurement(measurement: dict[str, object]) -> dict[str, object]:
        reported = measurement.copy()
        for field in ("database_execution_ms", "rendered_response_ms"):
            value = reported[field]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise CommandError("benchmark timing is invalid")
            reported[field] = round(float(value), 3)
        return reported

    def _measure_page(
        self,
        *,
        event: Event,
        page_number: int,
        filter_data: dict[str, str],
        capture_time_start: datetime | None,
        capture_time_end: datetime | None,
    ) -> dict[str, object]:
        page = gallery_page(
            event=event,
            page_number=str(page_number),
            capture_time_start=capture_time_start,
            capture_time_end=capture_time_end,
        )
        queryset = page.object_list
        if not isinstance(queryset, QuerySet):
            raise CommandError("gallery page did not retain a database queryset")
        plan = self._explain(queryset)
        rendered_response_ms = self._rendered_response_ms(
            event=event, page_number=page_number, filter_data=filter_data
        )
        return {
            "database_execution_ms": plan["execution_ms"],
            "plan_shape": plan["shape"],
            "rendered_response_ms": rendered_response_ms,
        }

    @staticmethod
    def _explain(queryset: QuerySet[Photo]) -> dict[str, object]:
        try:
            explained = queryset.explain(analyze=True, format="JSON")
            parsed = json.loads(explained)
            root = parsed[0]
            execution_ms = root["Execution Time"]
            plan = root["Plan"]
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise CommandError("PostgreSQL did not return a usable JSON execution plan") from error
        if not isinstance(execution_ms, (int, float)) or isinstance(execution_ms, bool):
            raise CommandError("PostgreSQL did not report execution time")
        return {
            "execution_ms": float(execution_ms),
            "shape": Command._plan_shape(plan),
        }

    @staticmethod
    def _plan_shape(plan: object) -> list[str]:
        if not isinstance(plan, dict):
            raise CommandError("PostgreSQL did not return a usable query plan")
        node_type = plan.get("Node Type")
        if not isinstance(node_type, str):
            raise CommandError("PostgreSQL query plan has no node type")
        nodes = [node_type]
        children = plan.get("Plans", [])
        if not isinstance(children, list):
            raise CommandError("PostgreSQL query plan has invalid child nodes")
        for child in children:
            nodes.extend(Command._plan_shape(child))
        return nodes

    @staticmethod
    def _rendered_response_ms(
        *,
        event: Event,
        page_number: int,
        filter_data: dict[str, str],
    ) -> float:
        query = {**filter_data, "page": str(page_number)}
        request = RequestFactory().get(
            reverse("event_detail", kwargs={"slug": event.slug}), data=query
        )
        request.user = AnonymousUser()
        started = perf_counter()
        try:
            response = event_detail(request, event.slug)
            content = response.content
        except Exception as error:
            raise CommandError("rendered event-detail request failed") from error
        if response.status_code != 200:
            raise CommandError("rendered event-detail response was non-200")
        _ = content
        return (perf_counter() - started) * 1_000

    @staticmethod
    def _ratio(*, baseline: object, measured: object) -> float | None:
        if not isinstance(baseline, (int, float)) or isinstance(baseline, bool):
            raise CommandError("unfiltered timing is invalid")
        if not isinstance(measured, (int, float)) or isinstance(measured, bool):
            raise CommandError("filtered timing is invalid")
        if baseline < 0 or measured < 0:
            raise CommandError("timings must not be negative")
        if baseline == 0:
            return 1.0 if measured == 0 else None
        return measured / baseline

    @staticmethod
    def _reported_ratio(value: float | None) -> float | None:
        return round(value, 3) if value is not None else None
