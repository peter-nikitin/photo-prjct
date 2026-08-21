from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import Resolver404, resolve, reverse
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.debug import SafeExceptionReporterFilter
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_GET, require_POST
from feature_flags import services as feature_flag_services
from picflow.gallery import GalleryPhoto, GalleryPhotoFactory, purchasable_paid_photo_queryset
from picflow.models import Event
from picflow.photo_policy import PAID_WATERMARKED_PREVIEWS_FLAG

from commerce.identity import parse_browser_token
from commerce.presentation import CartPresentation, cart_presentation_for_photos
from commerce.pricing import format_rub
from commerce.services import (
    CartMutationResult,
    CartSnapshot,
    clear_cart,
    read_cart,
    set_photo_selected,
)

PAID_PHOTO_CART_FLAG = "paid-photo-cart"
CART_COOKIE_NAME = "findme_cart"
CART_COOKIE_MAX_AGE = int(timedelta(days=30).total_seconds())


class CartExceptionReporterFilter(SafeExceptionReporterFilter):
    """Keep Django's default redaction and additionally hide the cart bearer."""

    sensitive_post_names = frozenset({"photo_id", "return_to"})
    sensitive_variable_names = frozenset(
        {
            "browser_token",
            "cart_presentation",
            "cart_state",
            "issued_browser_token",
            "issued_token",
            "photo_id",
            "public_token",
            "return_to",
        }
    )

    def get_safe_cookies(self, request: HttpRequest) -> dict[str, object]:
        cookies = super().get_safe_cookies(request)
        if CART_COOKIE_NAME in cookies:
            cookies[CART_COOKIE_NAME] = self.cleansed_substitute
        return cookies

    def get_post_parameters(self, request: HttpRequest):
        parameters = super().get_post_parameters(request)
        if not self.is_active(request):
            return parameters
        cleansed = parameters.copy()
        for name in self.sensitive_post_names:
            if name in cleansed:
                cleansed[name] = self.cleansed_substitute
        return cleansed

    def get_traceback_frame_variables(self, request: HttpRequest, tb_frame):
        return tuple(
            (name, self._cleanse_cart_traceback_value(name, value))
            for name, value in super().get_traceback_frame_variables(request, tb_frame)
        )

    def _cleanse_cart_traceback_value(self, name: object, value: object) -> object:
        if name in self.sensitive_variable_names or isinstance(
            value,
            (CartMutationResult, CartPresentation, CartSnapshot, RequestCartState),
        ):
            return self.cleansed_substitute
        if isinstance(value, dict):
            return {
                key: self._cleanse_cart_traceback_value(key, nested_value)
                for key, nested_value in value.items()
            }
        return value


@dataclass(frozen=True)
class RequestCartState:
    presentation: CartPresentation
    delete_browser_token: bool


def paid_cart_enabled(request: HttpRequest) -> bool:
    return feature_flag_services.is_enabled(PAID_PHOTO_CART_FLAG, request.user)


@sensitive_variables()
def cart_state_for_photos(
    *,
    request: HttpRequest,
    event: Event,
    photos: tuple[GalleryPhoto, ...],
    watermarked_previews_enabled: bool,
    require_eligible_photo: bool = False,
) -> RequestCartState | None:
    """Return personalized cart state only at the two independent enabled boundaries."""
    if not paid_cart_enabled(request) or not watermarked_previews_enabled:
        return None
    if not _event_is_cart_eligible(event):
        return None
    photo_ids = tuple(photo.photo_id for photo in photos)
    eligible_photo_ids = tuple(
        purchasable_paid_photo_queryset(
            event=event,
            watermarked_previews_enabled=watermarked_previews_enabled,
        )
        .filter(pk__in=photo_ids)
        .values_list("pk", flat=True)
    )
    if require_eligible_photo and not eligible_photo_ids:
        return None
    snapshot = read_cart(
        event=event,
        browser_token=_browser_token(request),
        watermarked_previews_enabled=watermarked_previews_enabled,
    )
    return RequestCartState(
        presentation=cart_presentation_for_photos(
            snapshot=snapshot,
            photos=photos,
            eligible_photo_ids=eligible_photo_ids,
        ),
        delete_browser_token=snapshot.delete_browser_token,
    )


def private_cart_response(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "private, no-store"
    patch_vary_headers(response, ("Cookie",))
    return response


def apply_read_cookie_decision(
    response: HttpResponse, *, delete_browser_token: bool
) -> HttpResponse:
    if delete_browser_token:
        _expire_browser_token(response)
    return response


@sensitive_variables()
@require_GET
def detail(request: HttpRequest, event_slug: str) -> HttpResponse:
    event, watermarked_previews_enabled = _authorized_event(request, event_slug=event_slug)
    if event is None:
        return _not_found()
    browser_token = _browser_token(request)
    snapshot = read_cart(
        event=event,
        browser_token=browser_token,
        watermarked_previews_enabled=watermarked_previews_enabled,
    )
    queryset = purchasable_paid_photo_queryset(
        event=event,
        watermarked_previews_enabled=watermarked_previews_enabled,
    ).filter(pk__in=snapshot.photo_ids)
    photos_by_id = {photo.pk: photo for photo in queryset}
    photos = tuple(
        GalleryPhotoFactory.from_photo(photo=photos_by_id[photo_id], event_slug=event.slug)
        for photo_id in snapshot.photo_ids
        if photo_id in photos_by_id
    )
    presentation = cart_presentation_for_photos(
        snapshot=snapshot,
        photos=photos,
        eligible_photo_ids=snapshot.photo_ids,
    )
    response = render(
        request,
        "commerce/cart.html",
        {
            "event": event,
            "cart_presentation": presentation,
            "yandex_metrika_counter_id": None,
        },
    )
    private_cart_response(response)
    return apply_read_cookie_decision(
        response,
        delete_browser_token=snapshot.delete_browser_token,
    )


@sensitive_variables()
@require_POST
def set_photo_state(request: HttpRequest, event_slug: str) -> HttpResponse:
    event, watermarked_previews_enabled = _authorized_event(request, event_slug=event_slug)
    photo_id = request.POST.get("photo_id")
    selected_value = request.POST.get("selected")
    if event is None or not photo_id or selected_value not in {"0", "1"}:
        return _not_found()
    if (
        not purchasable_paid_photo_queryset(
            event=event,
            watermarked_previews_enabled=watermarked_previews_enabled,
        )
        .filter(pk=photo_id)
        .exists()
    ):
        return _not_found()
    browser_token = _browser_token(request)
    result = set_photo_selected(
        event=event,
        photo_id=photo_id,
        selected=selected_value == "1",
        browser_token=browser_token,
        watermarked_previews_enabled=watermarked_previews_enabled,
    )
    response = _mutation_response(
        request,
        event=event,
        result=result,
        photo_id=photo_id,
        browser_token=browser_token,
    )
    return _apply_mutation_cookie(response, result=result, browser_token=browser_token)


@sensitive_variables()
@require_POST
def clear(request: HttpRequest, event_slug: str) -> HttpResponse:
    event, _watermarked_previews_enabled = _authorized_event(request, event_slug=event_slug)
    if event is None:
        return _not_found()
    browser_token = _browser_token(request)
    result = clear_cart(event=event, browser_token=browser_token)
    response = _mutation_response(
        request,
        event=event,
        result=result,
        photo_id=None,
        browser_token=browser_token,
    )
    return _apply_mutation_cookie(response, result=result, browser_token=browser_token)


def _authorized_event(request: HttpRequest, *, event_slug: str) -> tuple[Event | None, bool]:
    watermarked_previews_enabled = feature_flag_services.is_enabled(
        PAID_WATERMARKED_PREVIEWS_FLAG,
        request.user,
    )
    if not paid_cart_enabled(request) or not watermarked_previews_enabled:
        return None, watermarked_previews_enabled
    event = (
        Event.objects.site_visible_to(request.user)
        .filter(
            slug=event_slug,
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks__gt=0,
        )
        .only("id", "name", "slug", "access_type", "price_per_photo_kopecks")
        .first()
    )
    return event, watermarked_previews_enabled


def _event_is_cart_eligible(event: Event) -> bool:
    return Event.objects.filter(
        pk=event.pk,
        publication_status=Event.PublicationStatus.PUBLISHED,
        access_type=Event.AccessType.PAID,
        price_per_photo_kopecks__gt=0,
    ).exists()


def _browser_token(request: HttpRequest) -> str | None:
    token = request.COOKIES.get(CART_COOKIE_NAME)
    if token is None:
        return None
    try:
        parse_browser_token(token)
    except ValueError:
        return None
    return token


def _mutation_response(
    request: HttpRequest,
    *,
    event: Event,
    result: CartMutationResult,
    photo_id: str | None,
    browser_token: str | None,
) -> HttpResponse:
    if _json_requested(request):
        payload: dict[str, object] = {
            "selected": result.selected,
            **_snapshot_payload(result.snapshot),
        }
        if photo_id is not None:
            payload = {"photo_id": photo_id, **payload}
        response = JsonResponse(payload)
    else:
        response = redirect(_safe_return_path(request, event=event, browser_token=browser_token))
    return private_cart_response(response)


def _snapshot_payload(snapshot: CartSnapshot) -> dict[str, object]:
    return {
        "item_count": snapshot.item_count,
        "unit_price_kopecks": snapshot.unit_price_kopecks,
        "unit_price_display": format_rub(snapshot.unit_price_kopecks),
        "total_kopecks": snapshot.total_kopecks,
        "total_display": format_rub(snapshot.total_kopecks),
    }


def _json_requested(request: HttpRequest) -> bool:
    return any(
        value.partition(";")[0].strip().lower() == "application/json"
        for value in request.headers.get("Accept", "").split(",")
    )


def _safe_return_path(request: HttpRequest, *, event: Event, browser_token: str | None) -> str:
    requested = request.POST.get("return_to", "")
    if (
        requested.startswith("/")
        and not requested.startswith("//")
        and "\\" not in requested
        and all(ord(character) >= 32 and ord(character) != 127 for character in requested)
        and url_has_allowed_host_and_scheme(
            requested,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
        and _is_approved_return_path(requested, event=event)
        and (browser_token is None or browser_token not in requested)
    ):
        return requested
    return reverse("event_detail", kwargs={"slug": event.slug})


def _is_approved_return_path(requested: str, *, event: Event) -> bool:
    try:
        match = resolve(urlsplit(requested).path)
    except (Resolver404, ValueError):
        return False
    if match.view_name == "event_detail":
        return match.kwargs.get("slug") == event.slug
    if match.view_name in {"commerce:detail", "selfie_search:result"}:
        return match.kwargs.get("event_slug") == event.slug
    return False


def _apply_mutation_cookie(
    response: HttpResponse,
    *,
    result: CartMutationResult,
    browser_token: str | None,
) -> HttpResponse:
    if result.delete_browser_token:
        _expire_browser_token(response)
    elif result.refresh_browser_token:
        token = result.issued_browser_token or browser_token
        if token is not None:
            response.set_cookie(
                CART_COOKIE_NAME,
                token,
                max_age=CART_COOKIE_MAX_AGE,
                path="/",
                secure=True,
                httponly=True,
                samesite="Lax",
            )
    return response


def _expire_browser_token(response: HttpResponse) -> None:
    response.set_cookie(
        CART_COOKIE_NAME,
        "",
        max_age=0,
        expires="Thu, 01 Jan 1970 00:00:00 GMT",
        path="/",
        secure=True,
        httponly=True,
        samesite="Lax",
    )


def _not_found() -> HttpResponse:
    return private_cart_response(HttpResponse(status=404))
