import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from django import forms
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from picflow.gallery import purchasable_paid_photo_queryset
from picflow.models import Event

from commerce.capabilities import (
    PurchaseBrowserCapability,
    create_order_access_grant,
    issue_purchase_browser_capability,
    purchase_browser_authorizes_order,
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


class CheckoutError(Exception):
    pass


class CheckoutEmptyCart(CheckoutError):
    pass


class CheckoutEmailMismatch(CheckoutError):
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
    ) -> None:
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


@dataclass(frozen=True)
class _PreparedCheckout:
    order_id: int
    attempt_id: int
    request: PaymentRequest
    purchase_browser_capability: PurchaseBrowserCapability | None
    set_purchase_browser_cookie: bool


@sensitive_variables(
    "cart_browser_token",
    "purchase_browser_token",
    "checkout_email",
    "checkout_email_confirmation",
    "normalized_email",
    "normalized_confirmation",
    "capability",
    "prepared",
)
def create_checkout(
    *,
    event: Event,
    cart_browser_token: str | None,
    purchase_browser_token: str | None,
    checkout_email: str,
    checkout_email_confirmation: str,
    watermarked_previews_enabled: bool,
    purchase_enabled: bool,
    adapter_key: str,
    gateway: PaymentGateway,
    return_url_for_order: Callable[[str], str],
    now: datetime | None = None,
) -> CheckoutResult:
    """Create or reuse one immutable checkout, then call the gateway after commit."""
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
        checkout_email_confirmation=checkout_email_confirmation,
        watermarked_previews_enabled=watermarked_previews_enabled,
        purchase_enabled=purchase_enabled,
        adapter_key=adapter_key,
        return_url_for_order=return_url_for_order,
        now=current_time,
    )
    persisted_attempt = PaymentAttempt.objects.get(pk=prepared.attempt_id)
    if persisted_attempt.provider_payment_id and persisted_attempt.confirmation_url:
        return _checkout_result(prepared=prepared, attempt=persisted_attempt)

    try:
        created = gateway.create_payment(prepared.request)
    except PaymentGatewayError:
        raise _payment_unavailable(prepared) from None
    if not isinstance(created, CreatedPayment):
        raise _payment_unavailable(prepared)
    if (
        created.amount_kopecks != prepared.request.amount_kopecks
        or created.currency != prepared.request.currency
        or not _is_safe_hosted_confirmation_url(created.confirmation_url)
    ):
        raise _payment_unavailable(prepared)

    try:
        attempt = _reconcile_created_payment(
            attempt_id=prepared.attempt_id,
            created=created,
        )
    except CheckoutPaymentUnavailable:
        raise _payment_unavailable(prepared) from None
    return _checkout_result(prepared=prepared, attempt=attempt)


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
    checkout_email_confirmation: str,
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
        prepared = _prepare_locked_checkout(
            event=event,
            cart_digest=cart_digest,
            purchase_browser_token=purchase_browser_token,
            checkout_email=checkout_email,
            checkout_email_confirmation=checkout_email_confirmation,
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
    checkout_email_confirmation: str,
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
    pending_order = (
        Order.objects.select_for_update()
        .filter(
            event=authoritative_event,
            originating_cart_token_sha256=cart_digest,
            status=Order.Status.PENDING,
        )
        .first()
    )
    active_attempt = None
    if pending_order is not None:
        if not purchase_browser_authorizes_order(
            order=pending_order,
            token=purchase_browser_token,
            now=now,
        ):
            raise CheckoutPaymentUnavailable()
        locked_attempts = list(
            PaymentAttempt.objects.select_for_update()
            .filter(order=pending_order)
            .order_by("-created_at", "-pk")
        )
        active_attempt = next(
            (
                attempt
                for attempt in locked_attempts
                if attempt.status == PaymentAttempt.Status.PENDING
            ),
            None,
        )
        if active_attempt is not None and active_attempt.adapter_key != adapter_key:
            raise CheckoutPaymentUnavailable()

    current_items: list[CartItem] = []
    if active_attempt is None:
        if cart.expires_at <= now:
            _supersede_for_checkout_cart_mutation(pending_order)
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
        ineligible_item_ids = [
            item.pk for item in locked_items if item.photo_id not in eligible_ids
        ]
        if ineligible_item_ids:
            _supersede_for_checkout_cart_mutation(pending_order)
            pending_order = None
            CartItem.objects.filter(pk__in=ineligible_item_ids).delete()
        current_items = [item for item in locked_items if item.photo_id in eligible_ids]
        if not current_items:
            _supersede_for_checkout_cart_mutation(pending_order)
            cart.delete()
            return None

    normalized_email = _normalize_checkout_email(checkout_email)
    normalized_confirmation = _normalize_checkout_email(checkout_email_confirmation)
    if normalized_email != normalized_confirmation:
        raise CheckoutEmailMismatch()

    capability = None
    set_cookie = False
    if pending_order is not None and active_attempt is not None:
        order = pending_order
        attempt = active_attempt
    elif pending_order is not None:
        if _order_matches_checkout(
            order=pending_order,
            items=current_items,
            email=normalized_email,
            unit_price_kopecks=authoritative_event.price_per_photo_kopecks,
        ):
            order = pending_order
            attempt = _create_attempt(order=order, adapter_key=adapter_key)
        else:
            pending_order.status = Order.Status.SUPERSEDED
            pending_order.save(update_fields=["status"])
            order, attempt, capability = _create_order_and_attempt(
                event=authoritative_event,
                cart_digest=cart_digest,
                items=current_items,
                email=normalized_email,
                purchase_browser_token=purchase_browser_token,
                adapter_key=adapter_key,
                now=now,
            )
            set_cookie = True
    else:
        order, attempt, capability = _create_order_and_attempt(
            event=authoritative_event,
            cart_digest=cart_digest,
            items=current_items,
            email=normalized_email,
            purchase_browser_token=purchase_browser_token,
            adapter_key=adapter_key,
            now=now,
        )
        set_cookie = True

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
        set_purchase_browser_cookie=set_cookie,
    )


def _supersede_for_checkout_cart_mutation(order: Order | None) -> None:
    if order is None:
        return
    order.status = Order.Status.SUPERSEDED
    order.save(update_fields=["status"])


def _order_matches_checkout(
    *,
    order: Order,
    items: list[CartItem],
    email: str,
    unit_price_kopecks: int,
) -> bool:
    photo_ids = tuple(item.photo_id for item in items)
    saved_photo_ids = tuple(order.items.order_by("photo_id").values_list("photo_id", flat=True))
    return (
        order.checkout_email == email
        and order.total_kopecks == unit_price_kopecks * len(photo_ids)
        and saved_photo_ids == tuple(sorted(photo_ids))
        and all(
            item.unit_price_kopecks == unit_price_kopecks
            and item.line_total_kopecks == unit_price_kopecks
            for item in order.items.all()
        )
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
            if persisted_values != provider_values:
                raise CheckoutPaymentUnavailable()
            return attempt

        attempt.provider_payment_id = created.provider_payment_id
        attempt.confirmation_url = created.confirmation_url
        attempt.expires_at = created.expires_at
        attempt.reconciliation_next_attempt_at = (
            created.expires_at or attempt.created_at + timedelta(hours=24)
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
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username


def _payment_unavailable(prepared: _PreparedCheckout) -> CheckoutPaymentUnavailable:
    return CheckoutPaymentUnavailable(
        purchase_browser_capability=prepared.purchase_browser_capability,
        set_purchase_browser_cookie=prepared.set_purchase_browser_cookie,
    )
