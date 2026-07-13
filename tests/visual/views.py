"""Deterministic, database-free fixture views for visual review."""

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


@dataclass(frozen=True)
class FixtureImage:
    url: str


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

UPLOAD_QUEUE = (
    MappingProxyType(
        {
            "name": "IMG_1842_1048.jpg",
            "meta": "8,4 МБ · QR 1842",
            "status": "Готово",
            "image": PHOTOS[0].image,
        }
    ),
    MappingProxyType(
        {
            "name": "IMG_1842_1190.jpg",
            "meta": "7,9 МБ · EXIF 10:07",
            "status": "Распознано",
            "image": PHOTOS[1].image,
        }
    ),
    MappingProxyType(
        {
            "name": "finish_1316.jpg",
            "meta": "9,1 МБ · номер 516",
            "status": "Ожидает",
            "image": PHOTOS[2].image,
        }
    ),
)


def _render(request: HttpRequest, template: str, context: dict[str, Any]) -> HttpResponse:
    return render(request, template, context)


def catalog_populated(request: HttpRequest) -> HttpResponse:
    return _render(request, "catalog/event_catalog.html", {"events": EVENTS})


def catalog_empty(request: HttpRequest) -> HttpResponse:
    return _render(request, "catalog/event_catalog.html", {"events": ()})


def event_covered(request: HttpRequest) -> HttpResponse:
    return _render(request, "catalog/event_detail.html", {"event": EVENTS[0]})


def event_uncovered(request: HttpRequest) -> HttpResponse:
    return _render(request, "catalog/event_detail.html", {"event": EVENTS[1]})


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


def reference_upload(request: HttpRequest) -> HttpResponse:
    return _reference(request, "upload", queue=UPLOAD_QUEUE, events=EVENTS)


def reference_orders(request: HttpRequest) -> HttpResponse:
    return _reference(request, "orders", orders=ORDERS)


def reference_promotions(request: HttpRequest) -> HttpResponse:
    return _reference(request, "promotions", promotions=PROMOTIONS)


def reference_purchased(request: HttpRequest) -> HttpResponse:
    return _reference(request, "purchased", orders=ORDERS[:2], photos=PHOTOS[:3])
