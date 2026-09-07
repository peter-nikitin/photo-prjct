"""Deterministic, database-free fixture views for visual review."""

from dataclasses import dataclass, replace
from datetime import date, datetime
from types import MappingProxyType, SimpleNamespace
from typing import Any
from urllib.parse import urlencode

from commerce.forms import CheckoutForm
from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, JsonResponse, QueryDict
from django.shortcuts import render
from django.test import override_settings
from django.urls import reverse
from picflow.archive_presentation import archive_page_action
from picflow.forms import EventGalleryFolderFilterForm, EventGalleryTimeFilterForm
from selfie_search.forms import SelfieSearchUploadForm


@dataclass(frozen=True)
class FixtureImage:
    url: str


@dataclass(frozen=True)
class FixtureUser:
    username: str
    is_authenticated: bool = True
    is_active: bool = True
    is_staff: bool = False

    def get_username(self) -> str:
        return self.username

    def has_perms(self, permissions: tuple[str, ...]) -> bool:
        return all(self.has_perm(permission) for permission in permissions)

    def has_perm(self, permission: str) -> bool:
        return permission == "ingestion.upload_photos"


@dataclass(frozen=True)
class FixtureFolder:
    id: int
    name: str

    @property
    def pk(self) -> int:
        return self.id


@dataclass(frozen=True)
class FixtureFolderCollection:
    items: tuple[FixtureFolder, ...] = ()

    def all(self) -> tuple[FixtureFolder, ...]:
        return self.items


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
    timezone_name: str = "Europe/London"
    folders: FixtureFolderCollection = FixtureFolderCollection()
    publication_status: str = "published"

    @property
    def pk(self) -> str:
        return self.slug

    def get_access_type_display(self) -> str:
        return self.access_label


@dataclass(frozen=True)
class FixtureUploadBatch:
    id: str
    event_id: str
    event_name: str
    created_at: datetime
    last_activity_at: datetime
    expected_count: int
    confirmed_count: int
    failed_count: int
    unresolved_count: int
    can_close: bool
    processing: dict[str, Any]


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
class FixtureGalleryFace:
    face_number: int
    left_percent: float
    top_percent: float
    size_percent: float
    search_url: str


@dataclass(frozen=True)
class FixtureGalleryPhoto:
    photo_id: str
    preview_media_small: FixtureGalleryMedia
    preview_media_large: FixtureGalleryMedia
    download_url: str | None
    alt: str
    faces: tuple[FixtureGalleryFace, ...] = ()
    capture_time_display: str | None = None


@dataclass(frozen=True)
class FixtureCartPhotoPresentation:
    photo: FixtureGalleryPhoto
    selected: bool
    unit_price_display: str = "300 ₽"


@dataclass(frozen=True)
class FixtureCartPresentation:
    photos: tuple[FixtureCartPhotoPresentation, ...]
    item_count: int
    total_display: str
    pruned: bool = False


@dataclass(frozen=True)
class FixtureOrderPhotoPresentation:
    photo: FixtureGalleryPhoto
    unit_price_display: str = "300 ₽"


@dataclass(frozen=True)
class FixtureOrderPresentation:
    public_number: str
    created_at_display: str
    event_name: str
    status: str
    status_display: str
    total_display: str
    masked_delivery_email: str
    item_count: int
    photos: tuple[FixtureOrderPhotoPresentation, ...]


@dataclass(frozen=True)
class FixtureSelfieSearch:
    status: str
    eligible_photo_count: int = 0
    matched_photo_count: int = 0


@dataclass(frozen=True)
class FixtureSelfieSearchResult:
    pk: str


EVENTS = (
    FixtureEvent(
        "London 10K",
        "london-10k",
        "Лондон",
        date(2026, 6, 8),
        date(2026, 6, 9),
        "Городской забег с несколькими точками съёмки на трассе.",
        cover=FixtureImage("/static/images/run-city-1842.png"),
        folders=FixtureFolderCollection((FixtureFolder(4, "Старт"), FixtureFolder(8, "Финиш"))),
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

DRAFT_EVENT = replace(
    EVENTS[0],
    name="London 10K — предпросмотр",
    slug="london-10k-preview",
    publication_status="draft",
)


def _processing_summary(
    *, succeeded: int = 0, processing: int = 0, queued: int = 0, failed: int = 0
) -> dict[str, Any]:
    return {
        "total": succeeded + processing + queued + failed,
        "categories": {
            "succeeded": succeeded,
            "processing": processing,
            "queued": queued,
            "failed": failed,
        },
    }


UNFINISHED_UPLOADS = (
    FixtureUploadBatch(
        id="batch-resume-1",
        event_id="london-10k",
        event_name="London 10K",
        created_at=datetime(2026, 6, 8, 10, 0),
        last_activity_at=datetime(2026, 6, 9, 14, 30),
        expected_count=2,
        confirmed_count=1,
        failed_count=0,
        unresolved_count=1,
        can_close=False,
        processing=_processing_summary(queued=1),
    ),
)

ACTIVE_UPLOADS = (
    FixtureUploadBatch(
        id="batch-lifecycle",
        event_id="london-10k",
        event_name="London 10K",
        created_at=datetime(2026, 6, 9, 15, 0),
        last_activity_at=datetime(2026, 6, 9, 15, 2),
        expected_count=3,
        confirmed_count=1,
        failed_count=0,
        unresolved_count=2,
        can_close=False,
        processing=_processing_summary(queued=1),
    ),
)

PARTIAL_UPLOADS = (
    replace(
        ACTIVE_UPLOADS[0],
        confirmed_count=1,
        failed_count=2,
        unresolved_count=2,
        processing=_processing_summary(succeeded=1),
    ),
)

PROCESSING_UPLOADS = (
    replace(
        ACTIVE_UPLOADS[0],
        confirmed_count=3,
        unresolved_count=0,
        can_close=True,
        processing=_processing_summary(succeeded=1, processing=1, queued=1),
    ),
)

COMPLETE_UPLOADS = (
    replace(
        PROCESSING_UPLOADS[0],
        processing=_processing_summary(succeeded=3),
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


def _gallery_photo(
    photo_id: str,
    image: str,
    faces: tuple[FixtureGalleryFace, ...] = (),
    *,
    capture_time_display: str | None = None,
    downloadable: bool = True,
) -> FixtureGalleryPhoto:
    return FixtureGalleryPhoto(
        photo_id=photo_id,
        preview_media_small=FixtureGalleryMedia(image, "preview-small"),
        preview_media_large=FixtureGalleryMedia(image, "preview-large"),
        download_url=f"/__visual__/downloads/{photo_id}/" if downloadable else None,
        alt=f"Фото {photo_id} с события London 10K",
        faces=faces,
        capture_time_display=capture_time_display,
    )


GALLERY_PHOTOS = (
    _gallery_photo("1048", "/static/images/run-city-1842.png"),
    _gallery_photo("1190", "/static/images/run-track-1190.png"),
    _gallery_photo("1316", "/static/images/run-finish-1842.png"),
    _gallery_photo("3125", "/static/images/run-expo-3125.png"),
)

SELFIE_RESULT_PHOTOS = (
    _gallery_photo("1048", "/static/images/run-city-1842.png", capture_time_display="09:18"),
    _gallery_photo("1190", "/static/images/run-track-1190.png", capture_time_display="10:07"),
    _gallery_photo("1316", "/static/images/run-finish-1842.png"),
)

PAID_GALLERY_PHOTOS = tuple(replace(photo, download_url=None) for photo in GALLERY_PHOTOS)
PAID_SELFIE_RESULT_PHOTOS = tuple(
    replace(photo, download_url=None) for photo in SELFIE_RESULT_PHOTOS
)


def _cart_presentation(
    photos: tuple[FixtureGalleryPhoto, ...], *, selected_ids: tuple[str, ...] = ()
) -> FixtureCartPresentation:
    selected = frozenset(selected_ids)
    return FixtureCartPresentation(
        photos=tuple(
            FixtureCartPhotoPresentation(photo=photo, selected=photo.photo_id in selected)
            for photo in photos
        ),
        item_count=len(selected),
        total_display=f"{len(selected) * 300} ₽",
    )


def _order_presentation(
    *, status: str, photos: tuple[FixtureGalleryPhoto, ...], item_count: int
) -> FixtureOrderPresentation:
    status_display = {
        "pending": "Проверяем оплату",
        "paid": "Заказ оплачен",
    }[status]
    return FixtureOrderPresentation(
        public_number="FM-ABCDEFGH",
        created_at_display="18.06.2026",
        event_name=EVENTS[0].name,
        status=status,
        status_display=status_display,
        total_display=f"{item_count * 300} ₽",
        masked_delivery_email="a***a@example.com",
        item_count=item_count,
        photos=tuple(FixtureOrderPhotoPresentation(photo=photo) for photo in photos),
    )


def _gallery_face(
    photo_id: str,
    face_number: int,
    left_percent: float,
    top_percent: float,
    size_percent: float,
) -> FixtureGalleryFace:
    detection_id = f"00000000-0000-4000-8000-{int(photo_id):010d}{face_number:02d}"
    return FixtureGalleryFace(
        face_number=face_number,
        left_percent=left_percent,
        top_percent=top_percent,
        size_percent=size_percent,
        search_url=(f"/events/london-10k/photos/{photo_id}/similar-search/{detection_id}/"),
    )


GALLERY_FACE_PHOTOS = (
    _gallery_photo("1048", "/static/images/run-city-1842.png"),
    _gallery_photo(
        "1190",
        "/static/images/run-track-1190.png",
        (_gallery_face("1190", 1, 45, 45, 9),),
        capture_time_display="10:07",
    ),
    _gallery_photo(
        "1316",
        "/static/images/run-finish-1842.png",
        (
            _gallery_face("1316", 1, 22, 60, 9),
            _gallery_face("1316", 2, 75, 60, 9),
        ),
        capture_time_display="10:43",
    ),
    _gallery_photo(
        "3125",
        "/static/images/run-expo-3125.png",
        (
            _gallery_face("3125", 1, 22, 60, 9),
            _gallery_face("3125", 2, 46, 44, 10),
            _gallery_face("3125", 3, 75, 60, 9),
            _gallery_face("3125", 4, 70, 41, 8),
        ),
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

UPLOAD_LIMITS = MappingProxyType(
    {
        "max_files": 10_000,
        "max_files_label": "10 000",
        "max_file_bytes": 52_428_800,
        "max_file_megabytes": 50,
        "registration_chunk": 100,
        "concurrency": 4,
    }
)

QUEUE_GROUPS = (
    ("needs_attention", "Требуют внимания", True),
    ("uploading", "Загружаются", True),
    ("waiting", "Ожидают", False),
    ("uploaded", "Загружены", False),
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

FOLDER_UPLOAD_QUEUE = (
    MappingProxyType(
        {
            "name": "DSC_4298.jpg",
            "meta": "17,8 МБ",
            "folder_label": "Старт",
            "status": "Загружено",
            "status_class": "uploaded",
            "progress": 100,
        }
    ),
    MappingProxyType(
        {
            "name": "DSC_4299.jpg",
            "meta": "22,3 МБ",
            "folder_label": "Финиш",
            "status": "Передача · 68%",
            "status_class": "active",
            "progress": 68,
        }
    ),
    MappingProxyType(
        {
            "name": "DSC_4300.jpg",
            "meta": "20,6 МБ",
            "folder_label": "Без папки",
            "status": "Ожидает",
            "status_class": "pending",
            "progress": 0,
        }
    ),
)


def _render(request: HttpRequest, template: str, context: dict[str, Any]) -> HttpResponse:
    return render(request, template, context)


def _as_staff(request: HttpRequest) -> None:
    request.user = FixtureUser("Администратор", is_staff=True)


def _header_only_event(response: HttpResponse) -> HttpResponse:
    fixture_style = (
        b'\n    <style data-visual-header-only="true">.event-gallery { display: none; }</style>\n  '
    )
    response.content = response.content.replace(b"</head>", fixture_style + b"</head>", 1)
    return response


def _manual_time_filter_form(data=None) -> EventGalleryTimeFilterForm:
    return EventGalleryTimeFilterForm(EVENTS[0], data)


def _gallery_context(
    *, data=None, photos=GALLERY_FACE_PHOTOS, page_number: int = 1
) -> dict[str, Any]:
    manual_time_filter_form = _manual_time_filter_form(data)
    gallery_folder_choices = EVENTS[0].folders.all()
    gallery_folder_filter_form = EventGalleryFolderFilterForm(
        EVENTS[0], gallery_folder_choices, data, include_unfiled=True
    )
    gallery_folder_filter_form.is_valid()
    manual_time_filter_invalid = (
        manual_time_filter_form.is_requested and not manual_time_filter_form.is_valid()
    )
    gallery_page = None
    if not manual_time_filter_invalid:
        gallery_page = Paginator(photos, 3).page(page_number)
    else:
        photos = ()
    pagination_query_pairs = [
        ("folder", str(folder_id)) for folder_id in gallery_folder_filter_form.selected_folder_ids
    ]
    if gallery_folder_filter_form.include_unfiled:
        pagination_query_pairs.append(("unfiled", "1"))
    if manual_time_filter_form.is_requested and not manual_time_filter_invalid:
        if manual_time_filter_form.cleaned_data["from"]:
            pagination_query_pairs.append(("from", manual_time_filter_form.cleaned_data["from"]))
        if manual_time_filter_form.cleaned_data["to"]:
            pagination_query_pairs.append(("to", manual_time_filter_form.cleaned_data["to"]))
    return {
        "event": EVENTS[0],
        "gallery_photos": photos,
        "gallery_page": gallery_page,
        "manual_time_filter_form": manual_time_filter_form,
        "manual_time_filter_invalid": manual_time_filter_invalid,
        "gallery_folder_choices": gallery_folder_choices,
        "gallery_folder_filter_form": gallery_folder_filter_form,
        "gallery_filters_active": (
            (manual_time_filter_form.is_requested and not manual_time_filter_invalid)
            or gallery_folder_filter_form.is_requested
        ),
        "gallery_pagination_query": urlencode(pagination_query_pairs),
        "gallery_pagination_query_pairs": tuple(pagination_query_pairs),
        "selfie_search_form": SelfieSearchUploadForm(),
    }


def catalog_populated(request: HttpRequest) -> HttpResponse:
    return _render(request, "catalog/event_catalog.html", {"events": EVENTS})


def catalog_staff_preview(request: HttpRequest) -> HttpResponse:
    _as_staff(request)
    return _render(
        request,
        "catalog/event_catalog.html",
        {"events": (DRAFT_EVENT, EVENTS[1])},
    )


def catalog_empty(request: HttpRequest) -> HttpResponse:
    return _render(request, "catalog/event_catalog.html", {"events": ()})


def event_covered(request: HttpRequest) -> HttpResponse:
    return _header_only_event(_render(request, "catalog/event_detail.html", {"event": EVENTS[0]}))


def event_uncovered(request: HttpRequest) -> HttpResponse:
    return _header_only_event(_render(request, "catalog/event_detail.html", {"event": EVENTS[1]}))


def event_gallery_populated(request: HttpRequest) -> HttpResponse:
    return _render(request, "catalog/event_detail.html", _gallery_context())


def event_gallery_paid(request: HttpRequest) -> HttpResponse:
    context = _gallery_context(photos=PAID_GALLERY_PHOTOS)
    context["cart_presentation"] = _cart_presentation(PAID_GALLERY_PHOTOS, selected_ids=("1190",))
    return _render(
        request,
        "catalog/event_detail.html",
        context,
    )


def event_gallery_staff_preview(request: HttpRequest) -> HttpResponse:
    _as_staff(request)
    context = _gallery_context()
    context["event"] = DRAFT_EVENT
    return _render(request, "catalog/event_detail.html", context)


def event_gallery_empty(request: HttpRequest) -> HttpResponse:
    return _render(request, "catalog/event_detail.html", _gallery_context(photos=()))


def event_gallery_filtered_empty(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "catalog/event_detail.html",
        _gallery_context(
            data=QueryDict(
                "folder=4&folder=8&unfiled=1&from=2026-06-08T09%3A00&to=2026-06-08T10%3A00"
            ),
            photos=(),
        ),
    )


def event_gallery_manual_invalid(request: HttpRequest) -> HttpResponse:
    return _render(
        request, "catalog/event_detail.html", _gallery_context(data={"from": "not-a-time"})
    )


def visual_event_detail(request: HttpRequest) -> HttpResponse:
    if "from" in request.GET or "to" in request.GET:
        return _render(
            request,
            "catalog/event_detail.html",
            _gallery_context(data=request.GET, page_number=int(request.GET.get("page", "1"))),
        )
    return _render(
        request,
        "catalog/event_detail.html",
        _gallery_context(page_number=int(request.GET.get("page", "1"))),
    )


def event_selfie_search(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "catalog/event_detail.html",
        _gallery_context(photos=GALLERY_PHOTOS),
    )


def event_selfie_search_rejected(request: HttpRequest) -> HttpResponse:
    form = SelfieSearchUploadForm(
        files={"selfie": SimpleUploadedFile("selfie.gif", b"GIF89a", content_type="image/gif")}
    )
    form.is_valid()
    context = _gallery_context(photos=GALLERY_PHOTOS)
    context["selfie_search_form"] = form
    return _render(request, "catalog/event_detail.html", context)


def selfie_search_processing(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "selfie_search/result.html",
        {
            "event": EVENTS[0],
            "gallery_photos": (),
            "is_terminal": False,
            "search": FixtureSelfieSearch("processing"),
            "status_url": "/__visual__/event/selfie-search/processing-status/",
        },
    )


def selfie_search_processing_status(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "processing"})


def selfie_search_empty(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "selfie_search/result.html",
        {
            "event": EVENTS[0],
            "gallery_photos": (),
            "is_terminal": True,
            "search": FixtureSelfieSearch("ready", eligible_photo_count=46),
            "status_url": "",
        },
    )


def selfie_search_error(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "selfie_search/result.html",
        {
            "event": EVENTS[0],
            "gallery_photos": (),
            "is_terminal": True,
            "search": FixtureSelfieSearch("multiple_faces"),
            "status_url": "",
        },
    )


def _selfie_search_ready(
    request: HttpRequest,
    *,
    event: FixtureEvent,
    photos: tuple[FixtureGalleryPhoto, ...] = SELFIE_RESULT_PHOTOS,
    cart_presentation: FixtureCartPresentation | None = None,
    archive_available: bool = True,
) -> HttpResponse:
    selfie_search_page = Paginator(photos, 2).page(1)
    visible_photos = tuple(selfie_search_page.object_list)
    results = tuple(
        FixtureSelfieSearchResult(f"00000000-0000-4000-8000-00000000001{index}")
        for index in range(1, len(visible_photos) + 1)
    )
    archive_action = (
        archive_page_action(
            item_count=len(visible_photos),
            page_number=selfie_search_page.number,
            page_count=selfie_search_page.paginator.num_pages,
        )
        if archive_available
        else None
    )
    return _render(
        request,
        "selfie_search/result.html",
        {
            "event": event,
            "cart_presentation": cart_presentation,
            "archive_action": archive_action,
            "archive_url": (
                reverse(
                    "selfie_search:result_archive",
                    kwargs={
                        "event_slug": event.slug,
                        "public_token": "visual-ready-result",
                    },
                )
                if archive_action is not None
                else None
            ),
            "gallery_photos": visible_photos,
            "gallery_result_items": tuple(zip(results, visible_photos, strict=True)),
            "selfie_search_page": selfie_search_page,
            "is_terminal": True,
            "search": FixtureSelfieSearch("ready", eligible_photo_count=46, matched_photo_count=3),
            "status_url": "",
        },
    )


def selfie_search_ready(request: HttpRequest) -> HttpResponse:
    return _selfie_search_ready(request, event=EVENTS[0])


def selfie_search_ready_single(request: HttpRequest) -> HttpResponse:
    return _selfie_search_ready(request, event=EVENTS[0], photos=SELFIE_RESULT_PHOTOS[:2])


def selfie_search_ready_paid(request: HttpRequest) -> HttpResponse:
    return _selfie_search_ready(
        request,
        event=EVENTS[0],
        photos=PAID_SELFIE_RESULT_PHOTOS,
        cart_presentation=_cart_presentation(PAID_SELFIE_RESULT_PHOTOS, selected_ids=("1190",)),
        archive_available=False,
    )


def cart_populated(request: HttpRequest) -> HttpResponse:
    photos = PAID_GALLERY_PHOTOS[:2]
    return _render(
        request,
        "commerce/cart.html",
        {
            "event": EVENTS[0],
            "cart_presentation": _cart_presentation(
                photos, selected_ids=tuple(photo.photo_id for photo in photos)
            ),
            "purchase_enabled": True,
            "checkout_form": CheckoutForm(),
            "yandex_metrika_counter_id": None,
        },
    )


def cart_empty(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "commerce/cart.html",
        {
            "event": EVENTS[0],
            "cart_presentation": _cart_presentation(()),
            "yandex_metrika_counter_id": None,
        },
    )


def _order_context(
    *, status: str, photos: tuple[FixtureGalleryPhoto, ...], archive_available: bool
) -> dict[str, Any]:
    order_items_page = Paginator(photos, 2).page(1)
    visible_photos = tuple(order_items_page.object_list)
    archive_action = (
        archive_page_action(
            item_count=len(visible_photos),
            page_number=order_items_page.number,
            page_count=order_items_page.paginator.num_pages,
        )
        if archive_available
        else None
    )
    return {
        "event": EVENTS[0],
        "archive_action": archive_action,
        "archive_url": (
            reverse(
                "commerce:order_archive",
                kwargs={"public_number": "FM-ABCDEFGH"},
            )
            if archive_action is not None
            else None
        ),
        "order_items_page": order_items_page,
        "order_presentation": _order_presentation(
            status=status,
            photos=visible_photos,
            item_count=order_items_page.paginator.count,
        ),
        "support_contact": "support@example.com",
        "yandex_metrika_counter_id": None,
    }


def order_pending(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "commerce/order.html",
        _order_context(status="pending", photos=PAID_GALLERY_PHOTOS[:2], archive_available=False)
        | {"order_status_url": "/__visual__/order/pending/status/"},
    )


def order_pending_status(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "pending"})


def order_paid(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "commerce/order.html",
        _order_context(status="paid", photos=PAID_GALLERY_PHOTOS[:2], archive_available=True),
    )


def order_paid_multi(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "commerce/order.html",
        _order_context(status="paid", photos=PAID_GALLERY_PHOTOS[:3], archive_available=True),
    )


def order_email_failed(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "commerce/order.html",
        _order_context(status="paid", photos=PAID_GALLERY_PHOTOS[:2], archive_available=True)
        | {"resend_feedback": "Не удалось отправить письмо. Попробуйте ещё раз."},
    )


def selfie_search_ready_staff_preview(request: HttpRequest) -> HttpResponse:
    _as_staff(request)
    return _selfie_search_ready(request, event=DRAFT_EVENT)


def selfie_search_feedback_problem(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "selfie_search/result.html",
        {
            "event": EVENTS[0],
            "gallery_photos": (),
            "gallery_result_items": (),
            "is_terminal": True,
            "public_token_digest": "a" * 64,
            "search": FixtureSelfieSearch("no_face"),
            "status_url": "",
            "feedback": {
                "variant": "problem",
                "visible_result_count": 0,
                "url": "/__visual__/feedback/",
                "preview": True,
            },
            "selfie_feedback_enabled": True,
        },
    )


def selfie_search_feedback_marking(request: HttpRequest) -> HttpResponse:
    photos = SELFIE_RESULT_PHOTOS
    results = tuple(
        FixtureSelfieSearchResult(f"00000000-0000-4000-8000-00000000000{index}")
        for index in range(1, 4)
    )
    return _render(
        request,
        "selfie_search/result.html",
        {
            "event": EVENTS[0],
            "gallery_photos": photos,
            "gallery_result_items": tuple(zip(results, photos, strict=True)),
            "selfie_search_page": Paginator(photos, 2).page(1),
            "is_terminal": True,
            "public_token_digest": "b" * 64,
            "search": FixtureSelfieSearch("ready", eligible_photo_count=46, matched_photo_count=3),
            "status_url": "",
            "feedback": {
                "variant": "result_labels",
                "visible_result_count": 3,
                "url": "/__visual__/feedback/",
                "preview": True,
            },
            "selfie_feedback_enabled": True,
        },
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
    batch_history: tuple[FixtureUploadBatch, ...] = (),
    photo_import_enabled: bool = False,
    photo_import_history_enabled: bool = False,
    photo_import_urls: dict[str, str] | None = None,
) -> HttpResponse:
    request.user = FixtureUser("Анна Смирнова")
    with override_settings(PHOTO_UPLOAD_ENABLED=True):
        return _render(
            request,
            "picflow/event_management.html",
            {
                "event": SimpleNamespace(**vars(EVENTS[0]), pk=42),
                "folders": EVENTS[0].folders.all(),
                "can_upload": True,
                "can_inspect": False,
                "batch_page": Paginator(batch_history, 20).page(1),
                "resumable_batch_ids": tuple(row.id for row in batch_history if not row.can_close),
                "upload_limits": UPLOAD_LIMITS,
                "upload_state": state,
                "upload_summary": summary,
                "upload_queue_groups": _upload_queue_groups(queue),
                "unfinished_batches": batch_history,
                "photo_import_enabled": photo_import_enabled,
                "photo_import_history_enabled": photo_import_history_enabled,
                "photo_import_urls": photo_import_urls or {},
                "status_url": "/__visual__/workspace/status-api/?role=upload",
                "batch_history_url": "/__visual__/workspace/batch-history-api/",
            },
        )


def _upload_queue_groups(
    queue: tuple[MappingProxyType[str, Any], ...],
) -> tuple[MappingProxyType[str, Any], ...]:
    if not queue:
        return ()

    grouped = {key: [] for key, _, _ in QUEUE_GROUPS}
    for item in queue:
        status_class = item["status_class"]
        if status_class in {"failed", "needs_attention"}:
            key = "needs_attention"
        elif status_class == "active":
            key = "uploading"
        elif status_class == "uploaded":
            key = "uploaded"
        else:
            key = "waiting"
        grouped[key].append(item)

    return tuple(
        MappingProxyType(
            {
                "key": key,
                "label": label,
                "expanded": expanded,
                "count": len(grouped[key]),
                "items": tuple(grouped[key]),
            }
        )
        for key, label, expanded in QUEUE_GROUPS
    )


def upload_empty(request: HttpRequest) -> HttpResponse:
    return _upload(
        request,
        state="empty",
        summary={"progress": 0, "total": 0, "uploaded": 0, "failed": 0, "bytes": "0 Б"},
        batch_history=UNFINISHED_UPLOADS if request.GET.get("resume") else (),
    )


def upload_active(request: HttpRequest) -> HttpResponse:
    return _upload(
        request,
        state="active",
        summary={
            "progress": 56,
            "total": 3,
            "uploaded": 1,
            "failed": 0,
            "bytes": "29,2 из 59,2 МБ",
        },
        queue=ACTIVE_UPLOAD_QUEUE,
        batch_history=ACTIVE_UPLOADS,
    )


def upload_partial(request: HttpRequest) -> HttpResponse:
    return _upload(
        request,
        state="partial",
        summary={
            "progress": 100,
            "total": 3,
            "uploaded": 1,
            "failed": 2,
            "bytes": "17,8 из 60,7 МБ",
        },
        queue=PARTIAL_UPLOAD_QUEUE,
        batch_history=PARTIAL_UPLOADS,
    )


def upload_processing(request: HttpRequest) -> HttpResponse:
    return _upload(
        request,
        state="complete",
        summary={
            "progress": 100,
            "total": 3,
            "uploaded": 3,
            "failed": 0,
            "bytes": "60,7 МБ",
        },
        queue=COMPLETE_UPLOAD_QUEUE,
        batch_history=PROCESSING_UPLOADS,
    )


def upload_complete(request: HttpRequest) -> HttpResponse:
    return _upload(
        request,
        state="complete",
        summary={
            "progress": 100,
            "total": 3,
            "uploaded": 3,
            "failed": 0,
            "bytes": "60,7 МБ",
        },
        queue=COMPLETE_UPLOAD_QUEUE,
        batch_history=COMPLETE_UPLOADS,
    )


def upload_folders(request: HttpRequest) -> HttpResponse:
    return _upload(
        request,
        state="active",
        summary={"progress": 56, "total": 3, "uploaded": 1, "failed": 0, "bytes": "60,7 МБ"},
        queue=FOLDER_UPLOAD_QUEUE,
        batch_history=ACTIVE_UPLOADS,
    )


def upload_imports(request: HttpRequest) -> HttpResponse:
    return _upload(
        request,
        state="empty",
        summary={"progress": 0, "total": 0, "uploaded": 0, "failed": 0, "bytes": "0 Б"},
        photo_import_enabled=True,
        photo_import_history_enabled=True,
        photo_import_urls={
            "collection": "/__visual__/upload/imports-api/",
            "detail": "/__visual__/upload/imports-api/{batch}/",
            "items": "/__visual__/upload/imports-api/{batch}/items/",
            "retry": "/__visual__/upload/imports-api/{batch}/retry/",
        },
    )


def upload_imports_api(request: HttpRequest) -> JsonResponse:
    records = _visual_import_records()
    return JsonResponse(
        {
            "contract_version": 1,
            "imports": records,
            "pagination": {"page": 1, "page_size": 20, "total": len(records), "pages": 1},
        }
    )


def upload_import_detail_api(request: HttpRequest, batch: str) -> JsonResponse:
    record = next(item for item in _visual_import_records() if item["id"] == batch)
    return JsonResponse({"contract_version": 1, "batch": record})


def _visual_import_records() -> tuple[dict[str, Any], ...]:
    return (
        _visual_import("import-empty", "completed", "Без папки"),
        _visual_import("import-duplicates", "completed", "Старт", jpeg=8, duplicate=8),
        _visual_import(
            "import-active",
            "transferring",
            "Финиш",
            jpeg=48,
            directory=2,
            unsupported=3,
            imported=19,
            pending=29,
        ),
        _visual_import(
            "import-partial", "partial", "Старт", jpeg=12, imported=9, duplicate=1, error=2
        ),
        _visual_import("import-paused", "paused", "Без папки", jpeg=30, imported=11, pending=19),
        _visual_import(
            "import-completed",
            "completed",
            "Финиш",
            jpeg=24,
            imported=24,
            processing_active=True,
        ),
    )


def _visual_import(
    import_id: str,
    status: str,
    folder: str,
    *,
    jpeg: int = 0,
    directory: int = 0,
    unsupported: int = 0,
    imported: int = 0,
    duplicate: int = 0,
    error: int = 0,
    pending: int = 0,
    processing_active: bool = False,
) -> dict[str, Any]:
    return {
        "id": import_id,
        "status": status,
        "event": {"id": "london-10k", "name": "London 10K"},
        "folder": None if folder == "Без папки" else {"id": folder.lower(), "name": folder},
        "created_at": "2026-09-07T10:30:00+03:00",
        "completed_at": "2026-09-07T10:42:00+03:00" if status in {"completed", "partial"} else None,
        "counts": {
            "jpeg": jpeg,
            "directory": directory,
            "unsupported": unsupported,
            "imported": imported,
            "duplicate": duplicate,
            "error": error,
            "pending": pending,
        },
        "error_code": "",
        "processing_active": processing_active,
    }


def reference_orders(request: HttpRequest) -> HttpResponse:
    return _reference(request, "orders", orders=ORDERS)


def reference_promotions(request: HttpRequest) -> HttpResponse:
    return _reference(request, "promotions", promotions=PROMOTIONS)


def reference_purchased(request: HttpRequest) -> HttpResponse:
    return _reference(request, "purchased", orders=ORDERS[:2], photos=PHOTOS[:3])


def upload_chooser(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "ingestion/upload.html",
        {
            "events": [
                SimpleNamespace(**vars(item), pk=index) for index, item in enumerate(EVENTS, 42)
            ]
        },
    )


ADMIN_PHOTOS = (
    {
        "id": "anna-finish-a",
        "filename": "finish-1048.jpg",
        "is_hidden": False,
        "thumbnail_url": "/static/images/run-city-1842.png",
        "original_url": "/static/images/run-city-1842.png",
        "photographer": "Анна Смирнова",
        "uploader_id": "1",
        "folder_name": "Финиш",
        "folder_id": "8",
        "capture_time": datetime(2026, 6, 8, 9, 18),
        "processing": {"category_label": "Обработано", "stages": []},
    },
    {
        "id": "maxim-finish",
        "filename": "finish-1190.jpg",
        "is_hidden": False,
        "thumbnail_url": "/static/images/run-track-1190.png",
        "original_url": "/static/images/run-track-1190.png",
        "photographer": "Максим Орлов",
        "uploader_id": "2",
        "folder_name": "Финиш",
        "folder_id": "8",
        "capture_time": datetime(2026, 6, 8, 9, 21),
        "processing": {"category_label": "Обрабатывается", "stages": []},
    },
    {
        "id": "hidden-finish",
        "filename": "hidden-1842.jpg",
        "is_hidden": True,
        "thumbnail_url": "/static/images/run-finish-1842.png",
        "original_url": "/static/images/run-finish-1842.png",
        "photographer": "Анна Смирнова",
        "uploader_id": "1",
        "folder_name": "Финиш",
        "folder_id": "8",
        "capture_time": None,
        "processing": {"category_label": "Обработано", "stages": []},
    },
    {
        "id": "error-unfiled",
        "filename": "error-3125.jpg",
        "is_hidden": False,
        "thumbnail_url": None,
        "original_url": "/static/images/run-expo-3125.png",
        "photographer": None,
        "uploader_id": "",
        "folder_name": None,
        "folder_id": "",
        "capture_time": None,
        "processing": {
            "category_label": "Ошибка",
            "stages": [{"label": "Превью", "status_label": "Ошибка"}],
        },
    },
)

INTERACTION_PHOTOS = (
    ADMIN_PHOTOS[0],
    ADMIN_PHOTOS[1],
    {
        **ADMIN_PHOTOS[0],
        "id": "anna-finish-b",
        "filename": "finish-1301.jpg",
        "thumbnail_url": "/static/images/run-finish-1842.png",
        "original_url": "/static/images/run-finish-1842.png",
        "capture_time": datetime(2026, 6, 8, 9, 24),
    },
)


def _visual_filter_form(folders, data: QueryDict):
    class VisualFilterForm(forms.Form):
        folder = forms.MultipleChoiceField(
            required=False,
            choices=[(folder.pk, folder.name) for folder in folders],
            widget=forms.CheckboxSelectMultiple,
        )
        unfiled = forms.CharField(required=False)
        uploader = forms.MultipleChoiceField(
            required=False,
            choices=((1, "Анна Смирнова"), (2, "Максим Орлов")),
            widget=forms.CheckboxSelectMultiple,
        )
        uploader_unknown = forms.ChoiceField(required=False, choices=(("1", "Не указан"),))
        from_ = forms.CharField(required=False)
        to = forms.CharField(required=False)
        without_capture_time = forms.CharField(required=False)
        visibility = forms.ChoiceField(
            required=False,
            choices=(("all", "Все"), ("visible", "Видимые"), ("hidden", "Скрытые")),
        )
        processing = forms.MultipleChoiceField(
            required=False,
            choices=(
                ("processing", "Обрабатывается"),
                ("queued", "Ожидает обработки"),
                ("failed", "Ошибка"),
                ("cancelled", "Остановлена"),
                ("succeeded", "Обработано"),
                ("not_started", "Не запущена"),
                ("not_required", "Не требуется"),
            ),
            widget=forms.CheckboxSelectMultiple,
        )

        def __init__(self) -> None:
            super().__init__(data=data)
            self.fields["from"] = self.fields.pop("from_")
            for name in ("from", "to"):
                self.fields[name].widget.attrs.update(
                    min="2026-09-06T00:00", max="2026-09-06T23:59"
                )

    return VisualFilterForm()


def _canonical_visual_query(data: QueryDict) -> str:
    values = []
    for name in ("folder", "uploader", "processing"):
        values.extend((name, value) for value in data.getlist(name) if value)
    for name in ("unfiled", "uploader_unknown", "from", "to", "without_capture_time"):
        value = data.get(name)
        if value:
            values.append((name, value))
    visibility = data.get("visibility")
    if visibility and visibility != "all":
        values.append(("visibility", visibility))
    return urlencode(values)


def _event_photo_context(
    request: HttpRequest,
    *,
    scenario: str,
    combined: bool = False,
) -> dict[str, Any]:
    folders = EVENTS[0].folders.all()
    requested = request.GET.copy()
    if not requested.get("visibility"):
        requested["visibility"] = "all"
    photos = list(INTERACTION_PHOTOS if scenario == "interaction" else ADMIN_PHOTOS)
    if scenario == "filtered-empty":
        requested = QueryDict("folder=4&uploader=2&visibility=all")
        photos = []
    elif scenario == "hidden":
        requested = QueryDict("visibility=hidden")
        photos = [ADMIN_PHOTOS[2]]
    elif scenario == "error":
        requested = QueryDict("visibility=all&processing=failed")
        photos = [ADMIN_PHOTOS[3]]
    elif scenario == "interaction":
        uploader_ids = set(requested.getlist("uploader"))
        if uploader_ids:
            photos = [photo for photo in photos if photo["uploader_id"] in uploader_ids]

    page_size = 2 if scenario == "interaction" else 100
    photo_page = Paginator(photos, page_size).get_page(requested.get("page", 1))
    canonical_query = _canonical_visual_query(requested)
    canonical_url = request.path
    page_number = photo_page.number
    query_values = canonical_query
    if page_number > 1:
        query_values = (
            f"{query_values}&page={page_number}" if query_values else f"page={page_number}"
        )
    if query_values:
        canonical_url = f"{canonical_url}?{query_values}"

    context = {
        "event": SimpleNamespace(**vars(EVENTS[0]), pk=42),
        "folders": folders,
        "can_inspect": True,
        "can_upload": combined,
        "filters_valid": True,
        "filter_form": _visual_filter_form(folders, requested),
        "page_form": SimpleNamespace(page=SimpleNamespace(errors="")),
        "capabilities": SimpleNamespace(
            can_change_photos=True,
            can_add_folders=True,
            can_change_folders=True,
            can_delete_folders=True,
        ),
        "canonical_query": canonical_query,
        "canonical_url": canonical_url,
        "results_url": (
            "/__visual__/workspace/results/"
            if scenario == "interaction"
            else "/manage/events/42/photos/results/"
        ),
        "folder_create_url": (
            "/__visual__/workspace/folders/create/"
            if scenario == "interaction"
            else "/manage/events/42/photos/folders/create/"
        ),
        "folder_rename_url": "/manage/events/42/photos/folders/rename/",
        "folder_delete_url": "/manage/events/42/photos/folders/delete/",
        "photo_action_url": "/manage/events/42/photos/actions/",
        "photo_page": photo_page,
        "processing_summary": {
            "total": 4,
            "categories": {"processing": 1, "queued": 0, "failed": 1},
        },
        "status_url": (
            "/__visual__/workspace/status-api/"
            f"?role={'both' if combined else 'admin'}&scenario={scenario}"
        ),
        "batch_history_url": "/__visual__/workspace/batch-history-api/",
    }
    if combined:
        context.update(
            {
                "batch_page": Paginator(ACTIVE_UPLOADS, 20).page(1),
                "resumable_batch_ids": (ACTIVE_UPLOADS[0].id,),
                "upload_limits": UPLOAD_LIMITS,
                "upload_state": "active",
                "upload_summary": {
                    "progress": 56,
                    "total": 3,
                    "uploaded": 1,
                    "failed": 0,
                    "bytes": "29,2 из 59,2 МБ",
                },
                "upload_queue_groups": _upload_queue_groups(ACTIVE_UPLOAD_QUEUE),
                "unfinished_batches": ACTIVE_UPLOADS,
                "photo_import_enabled": False,
                "photo_import_history_enabled": False,
                "photo_import_urls": {},
            }
        )
    return context


def _event_photo_workspace(request: HttpRequest, *, scenario: str, combined: bool = False):
    request.user = FixtureUser("Администратор", is_staff=True)
    return _render(
        request,
        "picflow/event_management.html",
        _event_photo_context(request, scenario=scenario, combined=combined),
    )


def event_photo_workspace(request: HttpRequest) -> HttpResponse:
    context = _event_photo_context(request, scenario="populated")
    status_integration = request.GET.get("status_integration")
    if status_integration in {"1", "refresh"}:
        context.update(
            {
                "can_upload": True,
                "photo_page": Paginator(
                    [] if status_integration == "1" else list(ADMIN_PHOTOS),
                    100,
                ).page(1),
                "processing_summary": {
                    "total": 0,
                    "categories": {"processing": 0, "queued": 0, "failed": 0},
                },
                "batch_page": Paginator([], 20).page(1),
                "resumable_batch_ids": (),
                "upload_limits": UPLOAD_LIMITS,
                "upload_state": "empty",
                "upload_summary": {
                    "progress": 0,
                    "total": 0,
                    "uploaded": 0,
                    "failed": 0,
                    "bytes": "0 Б",
                },
                "upload_queue_groups": (),
                "unfinished_batches": (),
                "photo_import_enabled": status_integration == "1",
                "photo_import_history_enabled": status_integration == "1",
                "photo_import_urls": {
                    "collection": "/__visual__/upload/imports-api/",
                    "detail": "/__visual__/upload/imports-api/{batch}/",
                    "items": "/__visual__/upload/imports-api/{batch}/items/",
                    "retry": "/__visual__/upload/imports-api/{batch}/retry/",
                },
                "status_url": "/__visual__/workspace/status-api/?role=both",
            }
        )
    request.user = FixtureUser("Администратор", is_staff=True)
    return _render(request, "picflow/event_management.html", context)


def event_photo_workspace_filtered_empty(request: HttpRequest) -> HttpResponse:
    return _event_photo_workspace(request, scenario="filtered-empty")


def event_photo_workspace_hidden(request: HttpRequest) -> HttpResponse:
    return _event_photo_workspace(request, scenario="hidden")


def event_photo_workspace_error(request: HttpRequest) -> HttpResponse:
    return _event_photo_workspace(request, scenario="error")


def event_photo_workspace_interaction(request: HttpRequest) -> HttpResponse:
    return _event_photo_workspace(request, scenario="interaction", combined=True)


def event_photo_workspace_results(request: HttpRequest) -> HttpResponse:
    request.user = FixtureUser("Администратор", is_staff=True)
    context = _event_photo_context(request, scenario="interaction", combined=True)
    response = _render(request, "picflow/_event_photo_results.html", context)
    response.headers["X-Event-Photo-Canonical-Url"] = context["canonical_url"]
    return response


def event_photo_folder_create(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "folders": [
                {"id": folder.pk, "name": folder.name} for folder in EVENTS[0].folders.all()
            ]
            + [{"id": 12, "name": "Награждение"}]
        }
    )


def event_photo_status_api(request: HttpRequest) -> JsonResponse:
    role = request.GET.get("role", "both")
    scenario = request.GET.get("scenario", "populated")
    payload: dict[str, Any] = {
        "server_timestamp": "2026-09-07T10:00:00+03:00",
        "has_active_work": False,
        "capabilities": {
            "can_inspect": role in {"admin", "both"},
            "can_upload": role in {"upload", "both"},
        },
    }
    if role in {"admin", "both"}:
        filtered_result_count = {
            "filtered-empty": 0,
            "hidden": 1,
            "error": 1,
            "interaction": 3,
        }.get(scenario, 4)
        if scenario == "interaction" and request.GET.getlist("uploader"):
            filtered_result_count = sum(
                photo["uploader_id"] in request.GET.getlist("uploader")
                for photo in INTERACTION_PHOTOS
            )
        payload["admin"] = {
            "summary": {
                "total": 4,
                "categories": {"processing": 1, "queued": 0, "failed": 1},
            },
            "filtered_result_count": filtered_result_count,
            "result_list_changed": False,
            "photos": [],
        }
    if role in {"upload", "both"}:
        payload["batches"] = []
    return JsonResponse(payload)


def event_photo_batch_history_api(request: HttpRequest) -> HttpResponse:
    return HttpResponse(
        '<div data-batch-history-fragment data-batch-page="1">'
        "<p data-batch-history-empty>Сохранённых загрузок пока нет.</p></div>",
        content_type="text/html",
    )
