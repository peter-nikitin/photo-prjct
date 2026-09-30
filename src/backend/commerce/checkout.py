import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from picflow.gallery import purchasable_paid_photo_queryset
from picflow.models import Event

from commerce.capabilities import (
    PurchaseBrowserCapability,
    create_order_access_grant,
    issue_purchase_browser_capability,
)
from commerce.identity import browser_token_sha256
from commerce.models import Cart, CartItem, Order, OrderItem, PaymentAttempt
from commerce.payment_gateway import (
    CreatedPayment,
    PaymentGateway,
    PaymentGatewayError,
    PaymentReceiptLine,
    PaymentRequest,
)
from commerce.payments import PaymentTransitionRejected, reconcile_payment_attempt
from commerce.services import _lock_digest, consume_cart
from commerce.tbank_gateway import TBANK_ADAPTER_KEY


class CheckoutError(Exception):
    pass


class CheckoutEmptyCart(CheckoutError):
    pass


class CheckoutEmailInvalid(CheckoutError):
    pass


class CheckoutUnavailable(CheckoutError):
    pass


class CheckoutPaymentUnavailable(CheckoutError):
    def __init__(
        self,
        *,
        purchase_browser_capability: PurchaseBrowserCapability | None = None,
        set_purchase_browser_cookie: bool = False,
        cart_browser_token: str | None = None,
        order_public_number: str | None = None,
    ) -> None:
        self.cart_browser_token = cart_browser_token
        self.order_public_number = order_public_number
        self.purchase_browser_capability = purchase_browser_capability
        self.set_purchase_browser_cookie = set_purchase_browser_cookie
        super().__init__("Не удалось перейти к оплате. Попробуйте ещё раз.")


@dataclass(frozen=True)
class CheckoutResult:
    order: Order
    payment_attempt: PaymentAttempt
    confirmation_url: str
    return_url: str
    purchase_browser_capability: PurchaseBrowserCapability | None
    set_purchase_browser_cookie: bool
    cart_browser_token: str


@dataclass(frozen=True)
class _PreparedCheckout:
    order_id: int
    attempt_id: int
    request: PaymentRequest
    purchase_browser_capability: PurchaseBrowserCapability | None
    set_purchase_browser_cookie: bool
    cart_browser_token: str


@sensitive_variables(
    "cart_browser_token",
    "purchase_browser_token",
    "checkout_email",
    "normalized_email",
    "capability",
    "prepared",
)
def create_checkout(
    *,
    event: Event,
    cart_browser_token: str | None,
    purchase_browser_token: str | None,
    checkout_email: str,
    watermarked_previews_enabled: bool,
    purchase_enabled: bool,
    adapter_key: str,
    gateway: PaymentGateway,
    return_url_for_order: Callable[[str], str],
    now: datetime | None = None,
) -> CheckoutResult:
    """Consume one selection into an immutable Order, then call the gateway after commit."""
    current_time = now or timezone.now()
    if (
        not isinstance(adapter_key, str)
        or not adapter_key
        or len(adapter_key) > 64
        or getattr(gateway, "adapter_key", None) != adapter_key
    ):
        raise CheckoutPaymentUnavailable()
    prepared = _prepare_checkout(
        event=event,
        cart_browser_token=cart_browser_token,
        purchase_browser_token=purchase_browser_token,
        checkout_email=checkout_email,
        watermarked_previews_enabled=watermarked_previews_enabled,
        purchase_enabled=purchase_enabled,
        adapter_key=adapter_key,
        return_url_for_order=return_url_for_order,
        now=current_time,
    )
    try:
        attempt = initiate_payment(
            attempt_id=prepared.attempt_id,
            request=prepared.request,
            gateway=gateway,
            now=current_time,
        )
    except CheckoutPaymentUnavailable:
        raise _payment_unavailable(prepared) from None
    return _checkout_result(prepared=prepared, attempt=attempt)


def initiate_payment(
    *,
    attempt_id: int,
    request: PaymentRequest,
    gateway: PaymentGateway,
    now: datetime,
) -> PaymentAttempt:
    """Initiate a committed attempt without holding its Order database lock."""
    adapter_key = gateway.adapter_key
    current_time = now
    if adapter_key == TBANK_ADAPTER_KEY:
        # Commit the claim before network I/O. A crash at any later point requires CheckOrder.
        claimed = PaymentAttempt.objects.filter(
            pk=attempt_id,
            status=PaymentAttempt.Status.PENDING,
            initiation_started_at__isnull=True,
        ).update(
            initiation_started_at=current_time,
            reconciliation_next_attempt_at=current_time + timedelta(minutes=5),
        )
        if not claimed:
            try:
                reconcile_payment_attempt(attempt_id=attempt_id, gateway=gateway)
            except PaymentTransitionRejected:
                pass
            raise CheckoutPaymentUnavailable()
    try:
        created = gateway.create_payment(request)
    except ValueError:
        if adapter_key == TBANK_ADAPTER_KEY:
            # The bank adapter uses ValueError only for validation before any network request.
            PaymentAttempt.objects.filter(
                pk=attempt_id,
                status=PaymentAttempt.Status.PENDING,
            ).update(
                status=PaymentAttempt.Status.FAILED,
                terminal_at=timezone.now(),
                reconciliation_state=PaymentAttempt.ReconciliationState.PENDING,
                reconciliation_lease_id=None,
                reconciliation_lease_expires_at=None,
                reconciliation_next_attempt_at=None,
            )
        raise CheckoutPaymentUnavailable() from None
    except PaymentGatewayError:
        raise CheckoutPaymentUnavailable() from None
    if not isinstance(created, CreatedPayment):
        raise CheckoutPaymentUnavailable()
    if (
        created.amount_kopecks != request.amount_kopecks
        or created.currency != request.currency
        or not _is_safe_hosted_confirmation_url(created.confirmation_url)
    ):
        raise CheckoutPaymentUnavailable()

    try:
        attempt = _reconcile_created_payment(
            attempt_id=attempt_id,
            created=created,
        )
    except CheckoutPaymentUnavailable:
        raise CheckoutPaymentUnavailable() from None
    return attempt


def _normalize_checkout_email(value: str) -> str:
    try:
        normalized = forms.EmailField().clean(value)
    except forms.ValidationError:
        raise CheckoutEmailInvalid() from None
    return normalized.casefold()


def _prepare_checkout(
    *,
    event: Event,
    cart_browser_token: str | None,
    purchase_browser_token: str | None,
    checkout_email: str,
    watermarked_previews_enabled: bool,
    purchase_enabled: bool,
    adapter_key: str,
    return_url_for_order: Callable[[str], str],
    now: datetime,
) -> _PreparedCheckout:
    if not isinstance(adapter_key, str) or not adapter_key:
        raise CheckoutUnavailable()
    if not isinstance(cart_browser_token, str):
        raise CheckoutEmptyCart()
    try:
        cart_digest = browser_token_sha256(cart_browser_token)
    except (TypeError, ValueError):
        raise CheckoutEmptyCart() from None

    with transaction.atomic():
        if not purchase_enabled:
            raise CheckoutUnavailable()
        _lock_digest(cart_digest)
        prepared = _prepare_locked_checkout(
            event=event,
            cart_digest=cart_digest,
            purchase_browser_token=purchase_browser_token,
            checkout_email=checkout_email,
            watermarked_previews_enabled=watermarked_previews_enabled,
            adapter_key=adapter_key,
            return_url_for_order=return_url_for_order,
            now=now,
        )
    if prepared is None:
        raise CheckoutEmptyCart()
    return prepared


def _prepare_locked_checkout(
    *,
    event: Event,
    cart_digest: str,
    purchase_browser_token: str | None,
    checkout_email: str,
    watermarked_previews_enabled: bool,
    adapter_key: str,
    return_url_for_order: Callable[[str], str],
    now: datetime,
) -> _PreparedCheckout | None:
    authoritative_event = Event.objects.select_for_update().get(pk=event.pk)
    cart = (
        Cart.objects.select_for_update()
        .filter(
            event=authoritative_event,
            browser_token_sha256=cart_digest,
        )
        .first()
    )
    if cart is None:
        return None
    # An old token cannot resume payment or create another Order.
    if Order.objects.filter(
        event=authoritative_event,
        originating_cart_token_sha256=cart_digest,
        status=Order.Status.PENDING,
    ).exists():
        raise CheckoutEmptyCart()
    if cart.expires_at <= now:
        cart.delete()
        return None
    locked_items = list(
        CartItem.objects.select_for_update(of=("self",))
        .filter(cart=cart)
        .select_related("photo")
        .order_by("added_at", "photo_id")
    )
    eligible_ids = set(
        purchasable_paid_photo_queryset(
            event=authoritative_event,
            watermarked_previews_enabled=watermarked_previews_enabled,
        )
        .filter(pk__in=[item.photo_id for item in locked_items])
        .values_list("pk", flat=True)
    )
    CartItem.objects.filter(cart=cart).exclude(photo_id__in=eligible_ids).delete()
    current_items = [item for item in locked_items if item.photo_id in eligible_ids]
    if not current_items:
        cart.delete()
        return None
    normalized_email = _normalize_checkout_email(checkout_email)
    order, attempt, capability = _create_order_and_attempt(
        event=authoritative_event,
        cart_digest=cart_digest,
        items=current_items,
        email=normalized_email,
        purchase_browser_token=purchase_browser_token,
        adapter_key=adapter_key,
        now=now,
    )
    cart_browser_token = consume_cart(cart=cart, digest=cart_digest)
    request = _payment_request(
        order=order,
        attempt=attempt,
        return_url_for_order=return_url_for_order,
    )
    return _PreparedCheckout(
        order_id=order.pk,
        attempt_id=attempt.pk,
        request=request,
        purchase_browser_capability=capability,
        set_purchase_browser_cookie=True,
        cart_browser_token=cart_browser_token,
    )


def _create_order_and_attempt(
    *,
    event: Event,
    cart_digest: str,
    items: list[CartItem],
    email: str,
    purchase_browser_token: str | None,
    adapter_key: str,
    now: datetime,
) -> tuple[Order, PaymentAttempt, PurchaseBrowserCapability]:
    capability = issue_purchase_browser_capability(
        order_created_at=now,
        existing_token=purchase_browser_token,
    )
    unit_price = event.price_per_photo_kopecks
    order = Order.objects.create(
        event=event,
        originating_cart_token_sha256=cart_digest,
        purchase_browser_token_sha256=capability.token_sha256,
        checkout_email=email,
        total_kopecks=unit_price * len(items),
        currency="RUB",
    )
    OrderItem.objects.bulk_create(
        [
            OrderItem(
                order=order,
                photo=item.photo,
                photo_public_id=item.photo_id,
                unit_price_kopecks=unit_price,
                quantity=1,
                line_total_kopecks=unit_price,
            )
            for item in items
        ]
    )
    create_order_access_grant(
        order=order,
        source="checkout",
    )
    capability = issue_purchase_browser_capability(
        order_created_at=order.created_at,
        existing_token=capability.token,
    )
    return order, _create_attempt(order=order, adapter_key=adapter_key), capability


def _create_attempt(*, order: Order, adapter_key: str) -> PaymentAttempt:
    return PaymentAttempt.objects.create(
        order=order,
        amount_kopecks=order.total_kopecks,
        currency=order.currency,
        adapter_key=adapter_key,
        idempotency_key=secrets.token_urlsafe(32),
    )


def _payment_request(
    *,
    order: Order,
    attempt: PaymentAttempt,
    return_url_for_order: Callable[[str], str],
) -> PaymentRequest:
    receipt_lines = tuple(
        PaymentReceiptLine(
            description=(f"Original photo {item.photo_public_id} for personal non-commercial use"),
            quantity=1,
            unit_amount_kopecks=item.unit_price_kopecks,
            line_total_kopecks=item.line_total_kopecks,
        )
        for item in order.items.order_by("photo_id")
    )
    return PaymentRequest(
        order_public_number=order.public_number,
        amount_kopecks=order.total_kopecks,
        currency=order.currency,
        receipt_lines=receipt_lines,
        checkout_email=order.checkout_email,
        idempotency_key=attempt.idempotency_key,
        return_url=return_url_for_order(order.public_number),
    )


def _reconcile_created_payment(
    *,
    attempt_id: int,
    created: CreatedPayment,
) -> PaymentAttempt:
    with transaction.atomic():
        order_id = PaymentAttempt.objects.values_list("order_id", flat=True).get(pk=attempt_id)
        order = Order.objects.select_for_update().only("pk").get(pk=order_id)
        attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
        current_attempt = (
            PaymentAttempt.objects.select_for_update()
            .filter(order=order, status=PaymentAttempt.Status.PENDING)
            .first()
        )
        if attempt.status != PaymentAttempt.Status.PENDING or current_attempt != attempt:
            raise CheckoutPaymentUnavailable()
        if created.amount_kopecks != attempt.amount_kopecks or created.currency != attempt.currency:
            raise CheckoutPaymentUnavailable()

        provider_values = (
            created.provider_payment_id,
            created.confirmation_url,
            created.expires_at,
        )
        persisted_values = (
            attempt.provider_payment_id,
            attempt.confirmation_url,
            attempt.expires_at,
        )
        if (
            attempt.provider_payment_id
            or attempt.confirmation_url
            or attempt.expires_at is not None
        ):
            if persisted_values == provider_values:
                return attempt
            if not (
                attempt.adapter_key == TBANK_ADAPTER_KEY
                and attempt.provider_payment_id == created.provider_payment_id
                and not attempt.confirmation_url
                and attempt.expires_at is None
            ):
                raise CheckoutPaymentUnavailable()

        attempt.provider_payment_id = created.provider_payment_id
        attempt.confirmation_url = created.confirmation_url
        attempt.expires_at = created.expires_at
        attempt.reconciliation_next_attempt_at = (
            timezone.now() + timedelta(minutes=5)
            if attempt.adapter_key == TBANK_ADAPTER_KEY
            else created.expires_at or attempt.created_at + timedelta(hours=24)
        )
        attempt.save(
            update_fields=[
                "provider_payment_id",
                "confirmation_url",
                "expires_at",
                "reconciliation_next_attempt_at",
                "updated_at",
            ]
        )
        return attempt


def _checkout_result(*, prepared: _PreparedCheckout, attempt: PaymentAttempt) -> CheckoutResult:
    order = Order.objects.get(pk=prepared.order_id)
    return CheckoutResult(
        order=order,
        payment_attempt=attempt,
        confirmation_url=attempt.confirmation_url,
        return_url=prepared.request.return_url,
        purchase_browser_capability=prepared.purchase_browser_capability,
        set_purchase_browser_cookie=prepared.set_purchase_browser_cookie,
        cart_browser_token=prepared.cart_browser_token,
    )


def _is_safe_hosted_confirmation_url(value: object) -> bool:
    if (
        not isinstance(value, str)
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username:
        return True
    return (
        settings.DEBUG is True
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
        and bool(parsed.netloc)
        and not parsed.username
    )


def _payment_unavailable(prepared: _PreparedCheckout) -> CheckoutPaymentUnavailable:
    return CheckoutPaymentUnavailable(
        order_public_number=prepared.request.order_public_number,
        purchase_browser_capability=prepared.purchase_browser_capability,
        set_purchase_browser_cookie=prepared.set_purchase_browser_cookie,
        cart_browser_token=prepared.cart_browser_token,
    )
