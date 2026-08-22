from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import Resolver404, resolve, reverse
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.debug import SafeExceptionReporterFilter
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from feature_flags import services as feature_flag_services
from ingestion.storage import ObjectMissing, PrivateUploadStorage, StorageUnavailable
from picflow.gallery import (
    GALLERY_VARIANTS,
    GalleryPhoto,
    GalleryPhotoFactory,
    PublicMediaResolver,
    purchasable_paid_photo_queryset,
)
from picflow.models import Event, Photo
from picflow.photo_policy import PAID_WATERMARKED_PREVIEWS_FLAG

from commerce.capabilities import (
    purchase_browser_authorizes_order,
    record_order_customer_access,
    verify_order_access_grant,
)
from commerce.checkout import (
    CheckoutEmptyCart,
    CheckoutPaymentUnavailable,
    CheckoutUnavailable,
    create_checkout,
)
from commerce.delivery import ResendOrderAccessRateLimited, resend_order_access
from commerce.forms import CheckoutForm
from commerce.identity import parse_browser_token
from commerce.models import Order, OrderAccessGrant, OrderItem, PaymentAttempt
from commerce.original_delivery import (
    PurchasedOriginalDenied,
    PurchasedOriginalUnavailable,
    sign_purchased_original,
)
from commerce.payment_gateway import (
    IncomingPaymentNotification,
    PaymentGateway,
    PaymentGatewayError,
    PaymentRequest,
)
from commerce.payment_simulator import (
    PAYMENT_SIMULATOR_ADAPTER_KEY,
    PaymentSimulatorGateway,
    simulator_observation,
)
from commerce.payments import (
    PaymentTransitionRejected,
    apply_authenticated_notification,
    apply_payment_observation,
)
from commerce.presentation import CartPresentation, cart_presentation_for_photos, order_presentation
from commerce.pricing import format_rub
from commerce.services import (
    CartMutationResult,
    CartSnapshot,
    clear_cart,
    read_cart,
    set_photo_selected,
)

PAID_PHOTO_CART_FLAG = "paid-photo-cart"
PAID_PHOTO_PURCHASE_FLAG = "paid-photo-purchase"
PAYMENT_SIMULATOR_FLAG = "paid-photo-payment-simulator"
CART_COOKIE_NAME = "findme_cart"
CART_COOKIE_MAX_AGE = int(timedelta(days=30).total_seconds())
PURCHASE_COOKIE_NAME = "findme_purchase"
PURCHASE_COOKIE_MAX_AGE = int(timedelta(days=30).total_seconds())


class CartExceptionReporterFilter(SafeExceptionReporterFilter):
    """Keep Django's default redaction and additionally hide the cart bearer."""

    sensitive_post_names = frozenset({"email", "photo_id", "return_to"})
    sensitive_variable_names = frozenset(
        {
            "browser_token",
            "capability",
            "cart_presentation",
            "cart_browser_token",
            "cart_state",
            "checkout_email",
            "checkout_failure",
            "checkout_form",
            "checkout_result",
            "email",
            "form",
            "gateway",
            "issued_browser_token",
            "issued_token",
            "grant_identifier",
            "grant_signature",
            "notification",
            "normalized_email",
            "photo_id",
            "public_token",
            "purchase_browser_token",
            "prepared",
            "return_to",
            "signed_download",
            "signature",
        }
    )

    def get_safe_cookies(self, request: HttpRequest) -> dict[str, object]:
        cookies = super().get_safe_cookies(request)
        if CART_COOKIE_NAME in cookies:
            cookies[CART_COOKIE_NAME] = self.cleansed_substitute
        if PURCHASE_COOKIE_NAME in cookies:
            cookies[PURCHASE_COOKIE_NAME] = self.cleansed_substitute
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

    def get_safe_request_meta(self, request: HttpRequest) -> dict[str, object]:
        meta = super().get_safe_request_meta(request)
        if not getattr(request, "_is_commerce_order_bearer_request", False):
            return meta
        cleansed = meta.copy()
        for name in {"HTTP_REFERER", "PATH_INFO", "QUERY_STRING", "RAW_URI", "REQUEST_URI"}:
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
            (
                CartMutationResult,
                CartPresentation,
                CartSnapshot,
                CheckoutForm,
                PaymentRequest,
                RequestCartState,
            ),
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


def paid_purchase_enabled(request: HttpRequest) -> bool:
    return feature_flag_services.is_enabled(PAID_PHOTO_PURCHASE_FLAG, request.user)


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
            "purchase_enabled": paid_purchase_enabled(request),
            "checkout_form": CheckoutForm(),
            "yandex_metrika_counter_id": None,
        },
    )
    private_cart_response(response)
    return apply_read_cookie_decision(
        response,
        delete_browser_token=snapshot.delete_browser_token,
    )


@sensitive_post_parameters("email")
@sensitive_variables("checkout_failure", "checkout_form", "checkout_result", "gateway")
@require_http_methods(["GET", "POST"])
def checkout(request: HttpRequest, event_slug: str) -> HttpResponse:
    if not paid_purchase_enabled(request):
        return _purchase_not_found()
    event, watermarked_previews_enabled = _authorized_event(request, event_slug=event_slug)
    if event is None:
        return _purchase_not_found()
    if request.method == "GET":
        response = redirect("commerce:detail", event_slug=event.slug)
        return private_purchase_response(response)
    snapshot = read_cart(
        event=event,
        browser_token=_browser_token(request),
        watermarked_previews_enabled=watermarked_previews_enabled,
    )
    photos_by_id = {
        photo.pk: photo
        for photo in purchasable_paid_photo_queryset(
            event=event,
            watermarked_previews_enabled=watermarked_previews_enabled,
        ).filter(pk__in=snapshot.photo_ids)
    }
    photos = tuple(
        GalleryPhotoFactory.from_photo(photo=photos_by_id[photo_id], event_slug=event.slug)
        for photo_id in snapshot.photo_ids
        if photo_id in photos_by_id
    )
    presentation = cart_presentation_for_photos(
        snapshot=snapshot,
        photos=photos,
        eligible_photo_ids=photos_by_id,
    )
    if not presentation.photos:
        return _purchase_not_found()
    checkout_form = CheckoutForm(request.POST or None)
    checkout_failure: CheckoutPaymentUnavailable | None = None
    if request.method == "POST" and checkout_form.is_valid():
        try:
            gateway = _payment_gateway(request)
            checkout_result = create_checkout(
                event=event,
                cart_browser_token=_browser_token(request),
                purchase_browser_token=_purchase_browser_token(request),
                checkout_email=checkout_form.cleaned_data["email"],
                watermarked_previews_enabled=watermarked_previews_enabled,
                purchase_enabled=True,
                adapter_key=gateway.adapter_key,
                gateway=gateway,
                return_url_for_order=lambda public_number: request.build_absolute_uri(
                    reverse("commerce:order_return", kwargs={"public_number": public_number})
                ),
            )
        except CheckoutPaymentUnavailable as failure:
            checkout_failure = failure
            checkout_form.add_error(None, "Не удалось перейти к оплате. Попробуйте ещё раз.")
        except (CheckoutEmptyCart, CheckoutUnavailable):
            checkout_form.add_error(None, "Не удалось перейти к оплате. Попробуйте ещё раз.")
        else:
            response = redirect(checkout_result.confirmation_url)
            private_purchase_response(response)
            return _apply_purchase_cookie(
                response,
                token=(
                    checkout_result.purchase_browser_capability.token
                    if checkout_result.purchase_browser_capability is not None
                    else None
                ),
                should_set=checkout_result.set_purchase_browser_cookie,
            )
    response = render(
        request,
        "commerce/cart.html",
        {
            "event": event,
            "cart_presentation": presentation,
            "purchase_enabled": True,
            "checkout_form": checkout_form,
            "yandex_metrika_counter_id": None,
        },
    )
    private_purchase_response(response)
    response = apply_read_cookie_decision(
        response, delete_browser_token=snapshot.delete_browser_token
    )
    if checkout_failure is not None:
        return _apply_purchase_cookie(
            response,
            token=(
                checkout_failure.purchase_browser_capability.token
                if checkout_failure.purchase_browser_capability is not None
                else None
            ),
            should_set=checkout_failure.set_purchase_browser_cookie,
        )
    return response


def _payment_gateway(request: HttpRequest) -> PaymentGateway:
    """Select the feature-gated simulator until a real adapter is configured."""
    if not feature_flag_services.is_enabled(PAYMENT_SIMULATOR_FLAG, request.user):
        raise CheckoutPaymentUnavailable()
    return PaymentSimulatorGateway(
        confirmation_url_for_payment=lambda provider_payment_id: request.build_absolute_uri(
            reverse(
                "commerce:payment_simulator",
                kwargs={"provider_payment_id": provider_payment_id},
            )
        )
    )


@require_http_methods(["GET", "POST"])
def payment_simulator(request: HttpRequest, provider_payment_id: str) -> HttpResponse:
    if not feature_flag_services.is_enabled(
        PAID_PHOTO_PURCHASE_FLAG, request.user
    ) or not feature_flag_services.is_enabled(PAYMENT_SIMULATOR_FLAG, request.user):
        return _purchase_not_found()
    attempt = (
        PaymentAttempt.objects.select_related("order")
        .filter(
            adapter_key=PAYMENT_SIMULATOR_ADAPTER_KEY,
            provider_payment_id=provider_payment_id,
        )
        .first()
    )
    if attempt is None:
        return _purchase_not_found()
    if request.method == "POST":
        outcome = request.POST.get("outcome", "")
        try:
            observation = simulator_observation(
                attempt=attempt,
                outcome=outcome,
                provider_event_id=f"simulator-{attempt.pk}-{outcome}",
            )
            apply_payment_observation(
                attempt_id=attempt.pk,
                adapter_key=PAYMENT_SIMULATOR_ADAPTER_KEY,
                source="notification",
                observation=observation,
            )
        except (ValueError, PaymentTransitionRejected):
            return private_purchase_response(HttpResponse(status=400))
        return redirect(
            "commerce:order_return",
            public_number=attempt.order.public_number,
        )
    response = private_purchase_response(
        render(
            request,
            "commerce/payment_simulator.html",
            {
                "order": attempt.order,
                "attempt": attempt,
                "yandex_metrika_counter_id": None,
            },
        )
    )
    response["Referrer-Policy"] = "same-origin"
    return response


@require_GET
def order_return(request: HttpRequest, public_number: str) -> HttpResponse:
    return _render_order(request, public_number=public_number)


@require_GET
def order(request: HttpRequest, public_number: str) -> HttpResponse:
    return _render_order(request, public_number=public_number, record_customer_access=True)


@require_GET
def grant_order(
    request: HttpRequest,
    public_number: str,
    grant_identifier: str,
    signature: str,
) -> HttpResponse:
    return _render_order(
        request,
        public_number=public_number,
        grant_identifier=grant_identifier,
        grant_signature=signature,
        record_customer_access=True,
    )


@require_GET
def order_status(request: HttpRequest, public_number: str) -> HttpResponse:
    return _order_status(request, public_number=public_number)


@require_GET
def grant_order_status(
    request: HttpRequest,
    public_number: str,
    grant_identifier: str,
    signature: str,
) -> HttpResponse:
    return _order_status(
        request,
        public_number=public_number,
        grant_identifier=grant_identifier,
        grant_signature=signature,
    )


@csrf_exempt
@require_POST
@sensitive_variables()
def payment_notification(request: HttpRequest) -> HttpResponse:
    """Apply only adapter-authenticated provider evidence; browser CSRF is irrelevant here."""
    if not feature_flag_services.is_server_enabled(PAID_PHOTO_PURCHASE_FLAG):
        return _purchase_not_found()
    try:
        apply_authenticated_notification(
            gateway=_payment_gateway(request),
            notification=IncomingPaymentNotification(
                headers=request.headers,
                body=request.body,
            ),
        )
    except (
        CheckoutPaymentUnavailable,
        PaymentGatewayError,
        PaymentTransitionRejected,
    ):
        return _purchase_not_found()
    return private_purchase_response(HttpResponse(status=204))


@require_GET
def order_download(
    request: HttpRequest,
    public_number: str,
    photo_id: str,
) -> HttpResponse:
    return _download_order(
        request,
        public_number=public_number,
        photo_id=photo_id,
    )


@require_GET
def grant_order_download(
    request: HttpRequest,
    public_number: str,
    grant_identifier: str,
    signature: str,
    photo_id: str,
) -> HttpResponse:
    return _download_order(
        request,
        public_number=public_number,
        photo_id=photo_id,
        grant_identifier=grant_identifier,
        grant_signature=signature,
    )


@require_GET
def order_media(
    request: HttpRequest,
    public_number: str,
    photo_id: str,
    variant: str,
) -> HttpResponse:
    return _order_media(
        request,
        public_number=public_number,
        photo_id=photo_id,
        variant=variant,
    )


@require_GET
def grant_order_media(
    request: HttpRequest,
    public_number: str,
    grant_identifier: str,
    signature: str,
    photo_id: str,
    variant: str,
) -> HttpResponse:
    return _order_media(
        request,
        public_number=public_number,
        photo_id=photo_id,
        variant=variant,
        grant_identifier=grant_identifier,
        grant_signature=signature,
    )


@require_POST
def order_resend(request: HttpRequest, public_number: str) -> HttpResponse:
    return _resend_order(request, public_number=public_number)


@require_POST
def grant_order_resend(
    request: HttpRequest,
    public_number: str,
    grant_identifier: str,
    signature: str,
) -> HttpResponse:
    return _resend_order(
        request,
        public_number=public_number,
        grant_identifier=grant_identifier,
        grant_signature=signature,
    )


def _render_order(
    request: HttpRequest,
    *,
    public_number: str,
    grant_identifier: str | None = None,
    grant_signature: str | None = None,
    record_customer_access: bool = False,
) -> HttpResponse:
    authorized = _authorized_order(
        request,
        public_number=public_number,
        grant_identifier=grant_identifier,
        grant_signature=grant_signature,
    )
    if authorized is None:
        return _purchase_not_found()
    order_instance, access_grant = authorized
    support_contact = _configured_support_contact()
    if support_contact is None:
        return _purchase_not_found()
    response = render(
        request,
        "commerce/order.html",
        {
            "event": order_instance.event,
            "order_presentation": order_presentation(
                order=order_instance,
                media_url_builder=_order_media_url_builder(
                    order=order_instance,
                    access_grant=access_grant,
                    grant_signature=grant_signature,
                ),
            ),
            "access_grant": access_grant,
            "grant_signature": grant_signature if access_grant is not None else None,
            "support_contact": support_contact,
            "yandex_metrika_counter_id": None,
        },
    )
    response = private_purchase_response(response)
    if record_customer_access:
        record_order_customer_access(order=order_instance)
    return response


def _order_status(
    request: HttpRequest,
    *,
    public_number: str,
    grant_identifier: str | None = None,
    grant_signature: str | None = None,
) -> HttpResponse:
    authorized = _authorized_order(
        request,
        public_number=public_number,
        grant_identifier=grant_identifier,
        grant_signature=grant_signature,
    )
    if authorized is None:
        return _purchase_not_found()
    order_instance, _access_grant = authorized
    return private_purchase_response(JsonResponse({"status": order_instance.status}))


@sensitive_variables()
def _download_order(
    request: HttpRequest,
    *,
    public_number: str,
    photo_id: str,
    grant_identifier: str | None = None,
    grant_signature: str | None = None,
) -> HttpResponse:
    authorized = _authorized_order(
        request,
        public_number=public_number,
        grant_identifier=grant_identifier,
        grant_signature=grant_signature,
    )
    if authorized is None:
        return _purchase_not_found()
    order_instance, access_grant = authorized
    try:
        signed_download = sign_purchased_original(
            order=order_instance,
            photo_id=photo_id,
            purchase_browser_token=_purchase_browser_token(request),
            grant_identifier=access_grant.pk if access_grant is not None else grant_identifier,
            grant_signature=grant_signature,
            order_access_signing_secret=getattr(
                settings,
                "COMMERCE_ORDER_ACCESS_SIGNING_SECRET",
                "",
            ),
            storage=_purchased_original_storage(),
        )
    except PurchasedOriginalDenied:
        return _purchase_not_found()
    except (PurchasedOriginalUnavailable, StorageUnavailable):
        return private_purchase_response(HttpResponse(status=503))
    return private_purchase_response(redirect(signed_download.signed_url))


def _purchased_original_storage() -> PrivateUploadStorage:
    try:
        return PrivateUploadStorage()
    except (TypeError, ValueError):
        raise StorageUnavailable() from None


@sensitive_variables()
def _order_media(
    request: HttpRequest,
    *,
    public_number: str,
    photo_id: str,
    variant: str,
    grant_identifier: str | None = None,
    grant_signature: str | None = None,
) -> HttpResponse:
    authorized = _authorized_order(
        request,
        public_number=public_number,
        grant_identifier=grant_identifier,
        grant_signature=grant_signature,
    )
    if authorized is None or variant not in GALLERY_VARIANTS:
        return _purchase_not_found()
    order_instance, _access_grant = authorized
    item = (
        OrderItem.objects.select_related("photo")
        .filter(order=order_instance, photo_id=photo_id)
        .first()
    )
    if (
        item is None
        or item.photo.gallery_media_policy != Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
    ):
        return _purchase_not_found()
    try:
        signed_url = _purchased_watermarked_media_resolver().resolve_signed(
            photo=item.photo,
            variant=variant,
        )
    except ObjectMissing:
        return _purchase_not_found()
    except (StorageUnavailable, ValueError):
        return private_purchase_response(HttpResponse(status=503))
    return private_purchase_response(redirect(signed_url))


def _purchased_watermarked_media_resolver() -> PublicMediaResolver:
    try:
        return PublicMediaResolver(storage=PrivateUploadStorage())
    except (TypeError, ValueError):
        raise StorageUnavailable() from None


@sensitive_variables()
def _resend_order(
    request: HttpRequest,
    *,
    public_number: str,
    grant_identifier: str | None = None,
    grant_signature: str | None = None,
) -> HttpResponse:
    authorized = _authorized_order(
        request,
        public_number=public_number,
        grant_identifier=grant_identifier,
        grant_signature=grant_signature,
    )
    if authorized is None:
        return _purchase_not_found()
    order_instance, access_grant = authorized
    try:
        resend_order_access(order_id=order_instance.pk)
    except ResendOrderAccessRateLimited:
        return private_purchase_response(HttpResponse(status=429))
    except ValueError:
        return _purchase_not_found()
    if access_grant is not None and grant_signature is not None:
        destination = reverse(
            "commerce:grant_order",
            kwargs={
                "public_number": order_instance.public_number,
                "grant_identifier": access_grant.pk,
                "signature": grant_signature,
            },
        )
    else:
        destination = reverse(
            "commerce:order",
            kwargs={"public_number": order_instance.public_number},
        )
    return private_purchase_response(redirect(destination))


def _authorized_order(
    request: HttpRequest,
    *,
    public_number: str,
    grant_identifier: str | None = None,
    grant_signature: str | None = None,
) -> tuple[Order, OrderAccessGrant | None] | None:
    if not paid_purchase_enabled(request):
        return None
    order_instance = (
        Order.objects.select_related("event").filter(public_number=public_number).first()
    )
    if order_instance is None:
        return None
    if purchase_browser_authorizes_order(
        order=order_instance,
        token=_purchase_browser_token(request),
    ):
        return order_instance, None
    signing_secret = getattr(settings, "COMMERCE_ORDER_ACCESS_SIGNING_SECRET", "")
    try:
        access_grant = verify_order_access_grant(
            order=order_instance,
            grant_identifier=grant_identifier,
            signature=grant_signature,
            signing_secret=signing_secret,
        )
    except ValueError:
        access_grant = None
    if access_grant is None:
        return None
    return order_instance, access_grant


def _purchase_not_found() -> HttpResponse:
    return private_purchase_response(HttpResponse(status=404))


def _purchase_browser_token(request: HttpRequest) -> str | None:
    token = request.COOKIES.get(PURCHASE_COOKIE_NAME)
    if token is None:
        return None
    try:
        parse_browser_token(token)
    except ValueError:
        return None
    return token


def private_purchase_response(response: HttpResponse) -> HttpResponse:
    private_cart_response(response)
    response["Referrer-Policy"] = "no-referrer"
    return response


def _configured_support_contact() -> str | None:
    support_contact = getattr(settings, "COMMERCE_SUPPORT_CONTACT", "")
    if not isinstance(support_contact, str) or not support_contact.strip():
        return None
    return support_contact.strip()


def _order_media_url_builder(
    *,
    order: Order,
    access_grant: OrderAccessGrant | None,
    grant_signature: str | None,
):
    if access_grant is None or grant_signature is None:
        return lambda photo, variant: reverse(
            "commerce:order_media",
            kwargs={
                "public_number": order.public_number,
                "photo_id": photo.pk,
                "variant": variant,
            },
        )
    return lambda photo, variant: reverse(
        "commerce:grant_order_media",
        kwargs={
            "public_number": order.public_number,
            "grant_identifier": access_grant.pk,
            "signature": grant_signature,
            "photo_id": photo.pk,
            "variant": variant,
        },
    )


def _apply_purchase_cookie(
    response: HttpResponse,
    *,
    token: str | None,
    should_set: bool,
) -> HttpResponse:
    if should_set and token is not None:
        response.set_cookie(
            PURCHASE_COOKIE_NAME,
            token,
            max_age=PURCHASE_COOKIE_MAX_AGE,
            path="/",
            secure=True,
            httponly=True,
            samesite="Lax",
        )
    return response


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
