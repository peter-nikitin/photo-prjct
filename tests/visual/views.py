"""Deterministic, database-free fixture views for visual review."""

from dataclasses import dataclass, replace
from datetime import date, datetime
from types import MappingProxyType
from typing import Any
from urllib.parse import urlencode

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, JsonResponse, QueryDict
from django.shortcuts import render
from django.test import override_settings
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
class FixtureUnfinishedUpload:
    id: str
    event_id: str
    event_name: str
    created_at: datetime
    last_activity_at: datetime
    expected_count: int
    confirmed_count: int
    unresolved_count: int


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

UNFINISHED_UPLOADS = (
    FixtureUnfinishedUpload(
        id="batch-resume-1",
        event_id="london-10k",
        event_name="London 10K",
        created_at=datetime(2026, 6, 8, 10, 0),
        last_activity_at=datetime(2026, 6, 9, 14, 30),
        expected_count=2,
        confirmed_count=1,
        unresolved_count=1,
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
    return _render(
        request,
        "catalog/event_detail.html",
        _gallery_context(photos=PAID_GALLERY_PHOTOS),
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
) -> HttpResponse:
    results = tuple(
        FixtureSelfieSearchResult(f"00000000-0000-4000-8000-00000000001{index}")
        for index in range(1, 4)
    )
    return _render(
        request,
        "selfie_search/result.html",
        {
            "event": event,
            "gallery_photos": photos,
            "gallery_result_items": tuple(zip(results, photos, strict=True)),
            "selfie_search_page": Paginator(photos, 2).page(1),
            "is_terminal": True,
            "search": FixtureSelfieSearch("ready", eligible_photo_count=46, matched_photo_count=3),
            "status_url": "",
        },
    )


def selfie_search_ready(request: HttpRequest) -> HttpResponse:
    return _selfie_search_ready(request, event=EVENTS[0])


def selfie_search_ready_paid(request: HttpRequest) -> HttpResponse:
    return _selfie_search_ready(request, event=EVENTS[0], photos=PAID_SELFIE_RESULT_PHOTOS)


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
    unfinished_uploads: tuple[FixtureUnfinishedUpload, ...] = (),
    selected_event_id: str = "",
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
                "upload_queue_groups": _upload_queue_groups(queue),
                "unfinished_batches": unfinished_uploads,
                "selected_event_id": selected_event_id,
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
        unfinished_uploads=UNFINISHED_UPLOADS if request.GET.get("resume") else (),
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


def upload_folders(request: HttpRequest) -> HttpResponse:
    return _upload(
        request,
        state="active",
        summary={"progress": 56, "total": 3, "uploaded": 1, "failed": 0, "bytes": "60,7 МБ"},
        queue=FOLDER_UPLOAD_QUEUE,
        selected_event_id="london-10k",
    )


def reference_orders(request: HttpRequest) -> HttpResponse:
    return _reference(request, "orders", orders=ORDERS)


def reference_promotions(request: HttpRequest) -> HttpResponse:
    return _reference(request, "promotions", promotions=PROMOTIONS)


def reference_purchased(request: HttpRequest) -> HttpResponse:
    return _reference(request, "purchased", orders=ORDERS[:2], photos=PHOTOS[:3])
