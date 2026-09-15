"""Bounded, privacy-safe smoke for projection-backed public gallery reads."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from unittest.mock import patch

from config.views import event_detail
from django.contrib.auth.models import AnonymousUser
from django.contrib.staticfiles.storage import StaticFilesStorage, staticfiles_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Count
from django.test import RequestFactory
from django.urls import reverse
from feature_flags import services as feature_flag_services
from feature_flags.registry import PAID_WATERMARKED_PREVIEWS
from picflow.gallery import (
    GalleryMediaPurpose,
    gallery_photo_queryset,
    public_gallery_photo,
)
from picflow.models import Event

_FORBIDDEN_RELATIONS = (
    "processing_photoprocessingstate",
    "processing_processingattempt",
    "processing_photoderivative",
)


@dataclass(frozen=True)
class _CapturedQuery:
    sql: str
    params: object


@contextmanager
def _local_staticfiles_storage() -> Iterator[None]:
    previous_storage = staticfiles_storage._wrapped
    staticfiles_storage._wrapped = StaticFilesStorage()
    try:
        yield
    finally:
        staticfiles_storage._wrapped = previous_storage


@contextmanager
def _capture_sql() -> Iterator[list[_CapturedQuery]]:
    captured: list[_CapturedQuery] = []

    def wrapper(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        captured.append(_CapturedQuery(sql=sql, params=params))
        return result

    with connection.execute_wrapper(wrapper):
        yield captured


def _contains_processing_relation(queries: list[_CapturedQuery]) -> bool:
    sql = "\n".join(query.sql for query in queries).casefold()
    return any(relation in sql for relation in _FORBIDDEN_RELATIONS)


def _node_names(plan: object) -> list[str]:
    if not isinstance(plan, dict):
        raise CommandError("database did not return a usable query plan")
    node_type = plan.get("Node Type")
    if not isinstance(node_type, str):
        raise CommandError("database did not return a usable query plan")
    names = [node_type]
    children = plan.get("Plans", [])
    if not isinstance(children, list):
        raise CommandError("database did not return a usable query plan")
    for child in children:
        names.extend(_node_names(child))
    return names


def _plan_node_names(queries: list[_CapturedQuery]) -> list[str]:
    names: set[str] = set()
    try:
        with connection.cursor() as cursor:
            for query in queries:
                if not query.sql.lstrip().upper().startswith(("SELECT", "WITH")):
                    continue
                cursor.execute(f"EXPLAIN (ANALYZE, FORMAT JSON) {query.sql}", query.params)
                row = cursor.fetchone()
                if row is None:
                    raise CommandError("database did not return a usable query plan")
                parsed = row[0]
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
                if not isinstance(parsed, list) or not parsed:
                    raise CommandError("database did not return a usable query plan")
                root = parsed[0]
                if not isinstance(root, dict) or "Plan" not in root:
                    raise CommandError("database did not return a usable query plan")
                names.update(_node_names(root["Plan"]))
    except CommandError:
        raise
    except Exception:
        raise CommandError("database query-plan inspection failed") from None
    if not names:
        raise CommandError("database smoke captured no explainable reads")
    return sorted(names)


def _measurement(*, started: float, queries: list[_CapturedQuery]) -> dict[str, object]:
    if _contains_processing_relation(queries):
        raise CommandError("gallery-media smoke observed a processing relation")
    return {
        "elapsed_ms": round((perf_counter() - started) * 1_000, 3),
        "plan_node_names": _plan_node_names(queries),
        "query_count": len(queries),
    }


class Command(BaseCommand):
    help = "Smoke the largest public gallery page and one exact projection-backed media read."

    def handle(self, *args: Any, **options: Any) -> None:
        user = AnonymousUser()
        event = (
            Event.objects.site_visible_to(user)
            .filter(publication_status=Event.PublicationStatus.PUBLISHED)
            .annotate(smoke_photo_count=Count("photos"))
            .order_by("-smoke_photo_count", "pk")
            .first()
        )
        if event is None:
            self.stdout.write(
                json.dumps(
                    {"reason": "no_published_site_visible_event", "status": "skipped"},
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return

        paid_watermarked_previews_enabled = feature_flag_services.is_enabled(
            PAID_WATERMARKED_PREVIEWS, user
        )
        exact_photo_id = (
            gallery_photo_queryset(
                event=event,
                paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
            )
            .values_list("pk", flat=True)
            .first()
        )
        if exact_photo_id is None:
            raise CommandError("selected public event has no eligible gallery media")

        request = RequestFactory().get(
            reverse("event_detail", kwargs={"slug": event.slug}), data={"page": "1"}
        )
        request.user = user
        try:
            with (
                patch("config.views.gallery_search_faces_by_photo", return_value={}),
                _local_staticfiles_storage(),
                _capture_sql() as gallery_queries,
            ):
                gallery_started = perf_counter()
                response = event_detail(request, event.slug)
                _ = response.content
        except Exception:
            raise CommandError("gallery page smoke failed") from None
        if response.status_code != 200:
            raise CommandError("gallery page smoke returned a non-200 response")
        gallery_measurement = _measurement(started=gallery_started, queries=gallery_queries)

        try:
            with _capture_sql() as exact_queries:
                exact_started = perf_counter()
                public_gallery_photo(
                    event_id=event.pk,
                    photo_id=exact_photo_id,
                    purpose=GalleryMediaPurpose.PRESENTATION,
                    paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
                )
        except Exception:
            raise CommandError("exact gallery-media smoke failed") from None
        exact_measurement = _measurement(started=exact_started, queries=exact_queries)

        self.stdout.write(
            json.dumps(
                {
                    "exact_photo": exact_measurement,
                    "gallery_page": gallery_measurement,
                    "status": "ok",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
