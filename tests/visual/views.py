"""Deterministic, database-free fixture views for visual review."""

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.test import override_settings


@dataclass(frozen=True)
class FixtureImage:
    url: str


@dataclass(frozen=True)
class FixtureUser:
    username: str
    is_authenticated: bool = True

    def get_username(self) -> str:
        return self.username

    def has_perm(self, permission: str) -> bool:
        return permission == "ingestion.upload_photos"


@dataclass(frozen=True)
class FixtureEvent:
    name: str
    slug: str
    city: str
    start_date: date
    end_date: date
    description: str
    access_label: str = "Открытый доступ"
    cover: FixtureImage | None = None

    @property
    def pk(self) -> str:
        return self.slug

    def get_access_type_display(self) -> str:
        return self.access_label


@dataclass(frozen=True)
class FixturePhoto:
    photo_id: str
    image: str
    event: str
    zone: str
    time: str
    photographer: str
    bibs: tuple[str, ...]
    match: int
    price: int
    selected: bool = False


@dataclass(frozen=True)
class FixtureGalleryMedia:
    url: str
    variant: str


@dataclass(frozen=True)
class FixtureGalleryPhoto:
    photo_id: str
    preview_media_small: FixtureGalleryMedia
    preview_media_large: FixtureGalleryMedia
    alt: str


EVENTS = (
    FixtureEvent(
        "London 10K",
        "london-10k",
        "Лондон",
        date(2026, 6, 8),
        date(2026, 6, 9),
        "Городской забег с несколькими точками съёмки на трассе.",
        cover=FixtureImage("/static/images/run-city-1842.png"),
    ),
    FixtureEvent(
        "Brighton Ride",
        "brighton-ride",
        "Брайтон",
        date(2026, 6, 13),
        date(2026, 6, 14),
        "Велозаезд по набережной с отдельными зонами старта и финиша.",
    ),
    FixtureEvent(
        "Expo Run",
        "expo-run",
        "Expo Hall",
        date(2026, 6, 16),
        date(2026, 6, 17),
        "Портреты участников и финишные кадры из expo-зоны.",
        "Доступ по коду",
        FixtureImage("/static/images/run-expo-3125.png"),
    ),
)

PHOTOS = (
    FixturePhoto(
        "LDN-1048",
        "/static/images/run-city-1842.png",
        "London 10K",
        "Старт",
        "09:18",
        "Анна Смирнова",
        ("1842", "921"),
        86,
        350,
    ),
    FixturePhoto(
        "LDN-1190",
        "/static/images/run-track-1190.png",
        "London 10K",
        "Трасса",
        "10:07",
        "Анна Смирнова",
        ("1842", "2407"),
        93,
        350,
        True,
    ),
    FixturePhoto(
        "LDN-1316",
        "/static/images/run-finish-1842.png",
        "London 10K",
        "Финиш",
        "10:43",
        "Илья Волков",
        ("516", "1842"),
        78,
        350,
        True,
    ),
    FixturePhoto(
        "EXP-3125",
        "/static/images/run-expo-3125.png",
        "Expo Run",
        "Expo",
        "16:26",
        "Денис Орлов",
        ("1842", "44"),
        88,
        250,
    ),
)


def _gallery_photo(photo_id: str, image: str) -> FixtureGalleryPhoto:
    return FixtureGalleryPhoto(
        photo_id=photo_id,
        preview_media_small=FixtureGalleryMedia(image, "preview-small"),
        preview_media_large=FixtureGalleryMedia(image, "preview-large"),
        alt=f"Фото {photo_id} с события London 10K",
    )


GALLERY_PHOTOS = (
    _gallery_photo("1048", "/static/images/run-city-1842.png"),
    _gallery_photo("1190", "/static/images/run-track-1190.png"),
    _gallery_photo("1316", "/static/images/run-finish-1842.png"),
    _gallery_photo("3125", "/static/images/run-expo-3125.png"),
)

ORDERS = (
    MappingProxyType(
        {
            "id": "ORD-260618-001",
            "created": "18 июн. 2026, 10:12",
            "customer": "Demo Customer",
            "email": "customer@example.com",
            "status": "Оплачен",
            "status_class": "success",
            "items": 2,
            "total": "700 ₽",
        }
    ),
    MappingProxyType(
        {
            "id": "ORD-260618-002",
            "created": "18 июн. 2026, 11:04",
            "customer": "Runner 1842",
            "email": "runner1842@example.com",
            "status": "Новый",
            "status_class": "new",
            "items": 2,
            "total": "600 ₽",
        }
    ),
    MappingProxyType(
        {
            "id": "ORD-260617-003",
            "created": "17 июн. 2026, 16:38",
            "customer": "Brighton Ride",
            "email": "orders@example.com",
            "status": "В работе",
            "status_class": "warning",
            "items": 3,
            "total": "1 200 ₽",
        }
    ),
)

PROMOTIONS = (
    MappingProxyType(
        {
            "code": "ORG100",
            "name": "Организатор",
            "discount": "100%",
            "scope": "Весь сайт",
            "usage": "0 / 20",
            "active": True,
        }
    ),
    MappingProxyType(
        {
            "code": "LDN250",
            "name": "Скидка London",
            "discount": "250 ₽",
            "scope": "London 10K",
            "usage": "18 / 100",
            "active": True,
        }
    ),
    MappingProxyType(
        {
            "code": "MEDIA",
            "name": "Пакет для СМИ",
            "discount": "Пакет бесплатно",
            "scope": "Весь сайт",
            "usage": "4 / 10",
            "active": False,
        }
    ),
)

UPLOAD_LIMITS = MappingProxyType(
    {
        "max_files": 10_000,
        "max_files_label": "10 000",
        "max_file_bytes": 52_428_800,
        "max_file_megabytes": 50,
        "registration_chunk": 100,
        "concurrency": 4,
        "queue_window": 20,
    }
)

ACTIVE_UPLOAD_QUEUE = (
    MappingProxyType(
        {
            "name": "DSC_4182.jpg",
            "meta": "18,4 МБ",
            "status": "Загружено",
            "status_class": "uploaded",
            "progress": 100,
        }
    ),
    MappingProxyType(
        {
            "name": "DSC_4183.jpg",
            "meta": "21,7 МБ",
            "status": "Передача · 68%",
            "status_class": "active",
            "progress": 68,
        }
    ),
    MappingProxyType(
        {
            "name": "DSC_4184.jpg",
            "meta": "19,1 МБ",
            "status": "Ожидает",
            "status_class": "pending",
            "progress": 0,
        }
    ),
)

PARTIAL_UPLOAD_QUEUE = (
    MappingProxyType(
        {
            "name": "DSC_4298.jpg",
            "meta": "17,8 МБ",
            "status": "Загружено",
            "status_class": "uploaded",
            "progress": 100,
        }
    ),
    MappingProxyType(
        {
            "name": "DSC_4299.jpg",
            "meta": "22,3 МБ",
            "status": "Ошибка",
            "status_class": "failed",
            "progress": 61,
            "error": "Соединение прервано. Файл можно отправить ещё раз.",
            "retry": True,
        }
    ),
    MappingProxyType(
        {
            "name": "DSC_4300.jpg",
            "meta": "20,6 МБ",
            "status": "Ошибка",
            "status_class": "failed",
            "progress": 0,
            "error": "Хранилище временно недоступно.",
            "retry": True,
        }
    ),
)

COMPLETE_UPLOAD_QUEUE = (
    MappingProxyType(
        {
            "name": "DSC_4298.jpg",
            "meta": "17,8 МБ",
            "status": "Загружено",
            "status_class": "uploaded",
            "progress": 100,
        }
    ),
    MappingProxyType(
        {
            "name": "DSC_4299.jpg",
            "meta": "22,3 МБ",
            "status": "Загружено",
            "status_class": "uploaded",
            "progress": 100,
        }
    ),
    MappingProxyType(
        {
            "name": "DSC_4300.jpg",
            "meta": "20,6 МБ",
            "status": "Загружено",
            "status_class": "uploaded",
            "progress": 100,
        }
    ),
)


def _render(request: HttpRequest, template: str, context: dict[str, Any]) -> HttpResponse:
    return render(request, template, context)


def _header_only_event(response: HttpResponse) -> HttpResponse:
    fixture_style = (
        b'\n    <style data-visual-header-only="true">.event-gallery { display: none; }</style>\n  '
    )
    response.content = response.content.replace(b"</head>", fixture_style + b"</head>", 1)
    return response


def catalog_populated(request: HttpRequest) -> HttpResponse:
    return _render(request, "catalog/event_catalog.html", {"events": EVENTS})


def catalog_empty(request: HttpRequest) -> HttpResponse:
    return _render(request, "catalog/event_catalog.html", {"events": ()})


def event_covered(request: HttpRequest) -> HttpResponse:
    return _header_only_event(_render(request, "catalog/event_detail.html", {"event": EVENTS[0]}))


def event_uncovered(request: HttpRequest) -> HttpResponse:
    return _header_only_event(_render(request, "catalog/event_detail.html", {"event": EVENTS[1]}))


def event_gallery_populated(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "catalog/event_detail.html",
        {"event": EVENTS[0], "gallery_photos": GALLERY_PHOTOS},
    )


def event_gallery_empty(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "catalog/event_detail.html",
        {"event": EVENTS[0], "gallery_photos": ()},
    )


def legal(request: HttpRequest) -> HttpResponse:
    return _render(request, "ui/legal.html", {})


def _reference(request: HttpRequest, screen: str, **context: Any) -> HttpResponse:
    return _render(
        request,
        f"design_reference/{screen}.html",
        {"active_screen": screen, **context},
    )


def reference_search(request: HttpRequest) -> HttpResponse:
    return _reference(request, "search", photos=PHOTOS, selected=PHOTOS[1:3], events=EVENTS)


def reference_dashboard(request: HttpRequest) -> HttpResponse:
    return _reference(request, "dashboard", photos=PHOTOS, events=EVENTS, orders=ORDERS)


def reference_events(request: HttpRequest) -> HttpResponse:
    return _reference(request, "events", events=EVENTS)


def _upload(
    request: HttpRequest,
    *,
    state: str,
    summary: dict[str, int | str],
    queue: tuple[MappingProxyType[str, Any], ...] = (),
) -> HttpResponse:
    request.user = FixtureUser("Анна Смирнова")
    with override_settings(PHOTO_UPLOAD_ENABLED=True):
        return _render(
            request,
            "ingestion/upload.html",
            {
                "events": EVENTS,
                "upload_limits": UPLOAD_LIMITS,
                "upload_state": state,
                "upload_summary": summary,
                "upload_queue": queue,
            },
        )


def upload_empty(request: HttpRequest) -> HttpResponse:
    return _upload(
        request,
        state="empty",
        summary={"progress": 0, "total": 0, "uploaded": 0, "failed": 0, "bytes": "0 Б"},
    )


def upload_active(request: HttpRequest) -> HttpResponse:
    return _upload(
        request,
        state="active",
        summary={
            "progress": 36,
            "total": 128,
            "uploaded": 41,
            "failed": 0,
            "bytes": "2,1 из 5,8 ГБ",
        },
        queue=ACTIVE_UPLOAD_QUEUE,
    )


def upload_partial(request: HttpRequest) -> HttpResponse:
    return _upload(
        request,
        state="partial",
        summary={
            "progress": 100,
            "total": 128,
            "uploaded": 124,
            "failed": 4,
            "bytes": "5,6 ГБ",
        },
        queue=PARTIAL_UPLOAD_QUEUE,
    )


def upload_complete(request: HttpRequest) -> HttpResponse:
    return _upload(
        request,
        state="complete",
        summary={
            "progress": 100,
            "total": 128,
            "uploaded": 128,
            "failed": 0,
            "bytes": "5,8 ГБ",
        },
        queue=COMPLETE_UPLOAD_QUEUE,
    )


def reference_orders(request: HttpRequest) -> HttpResponse:
    return _reference(request, "orders", orders=ORDERS)


def reference_promotions(request: HttpRequest) -> HttpResponse:
    return _reference(request, "promotions", promotions=PROMOTIONS)


def reference_purchased(request: HttpRequest) -> HttpResponse:
    return _reference(request, "purchased", orders=ORDERS[:2], photos=PHOTOS[:3])
