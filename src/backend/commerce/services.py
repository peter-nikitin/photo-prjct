from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import connection, transaction
from django.utils import timezone
from picflow.gallery import purchasable_paid_photo_queryset
from picflow.models import Event, Photo

from commerce.identity import browser_token_sha256, generate_browser_token
from commerce.models import Cart, CartItem, Order, PaymentAttempt
from commerce.pricing import calculate_cart_pricing

CART_TTL = timedelta(days=30)


@dataclass(frozen=True)
class CartSnapshot:
    photo_ids: tuple[str, ...]
    unit_price_kopecks: int
    item_count: int
    total_kopecks: int
    pruned: bool = False
    delete_browser_token: bool = False
    mutation_locked: bool = False
    pending_order_public_number: str | None = None


@dataclass(frozen=True)
class CartMutationResult:
    snapshot: CartSnapshot
    selected: bool
    changed: bool
    issued_browser_token: str | None
    refresh_browser_token: bool
    delete_browser_token: bool


@dataclass(frozen=True)
class _OriginOrderState:
    order: Order | None = None
    mutation_locked: bool = False
    supersede_on_mutation: bool = False


def read_cart(
    *,
    event: Event,
    browser_token: str | None,
    watermarked_previews_enabled: bool,
    now: datetime | None = None,
) -> CartSnapshot:
    """Return the current eligible selection without extending its retention."""
    current_time = now or timezone.now()
    digest = _digest_or_none(browser_token)
    with transaction.atomic():
        if digest is not None:
            _lock_digest(digest)
        authoritative_event = _locked_event(event)
        if digest is None:
            return _snapshot(event=authoritative_event)
        cart = _locked_current_cart(
            event=authoritative_event,
            digest=digest,
            now=current_time,
            return_expired=True,
        )
        if cart is None:
            return _snapshot(event=authoritative_event)
        order_state = _locked_origin_order_state(event=authoritative_event, digest=digest)
        if cart.expires_at <= current_time:
            if order_state.mutation_locked:
                return _raw_locked_snapshot(
                    cart=cart,
                    event=authoritative_event,
                    order_state=order_state,
                )
            return _snapshot(event=authoritative_event)
        return _pruned_snapshot(
            cart=cart,
            event=authoritative_event,
            digest=digest,
            watermarked_previews_enabled=watermarked_previews_enabled,
            now=current_time,
            order_state=order_state,
        )


def set_photo_selected(
    *,
    event: Event,
    photo_id: str,
    selected: bool,
    browser_token: str | None,
    watermarked_previews_enabled: bool,
    now: datetime | None = None,
) -> CartMutationResult:
    """Set one photo's desired selection state and return the authoritative event snapshot."""
    current_time = now or timezone.now()
    digest = _digest_or_none(browser_token)
    issued_token = None
    with transaction.atomic():
        if digest is not None:
            _lock_digest(digest)
        authoritative_event = _locked_event(event)
        if selected:
            target_photo = (
                Photo.objects.select_for_update()
                .filter(
                    pk=photo_id,
                    event=authoritative_event,
                )
                .first()
            )
            if (
                target_photo is None
                or not purchasable_paid_photo_queryset(
                    event=authoritative_event,
                    watermarked_previews_enabled=watermarked_previews_enabled,
                )
                .filter(pk=target_photo.pk)
                .exists()
            ):
                return _mutation_result(
                    snapshot=_snapshot(event=authoritative_event),
                    selected=False,
                )

        if digest is None:
            if not selected:
                return _mutation_result(
                    snapshot=_snapshot(event=authoritative_event),
                    selected=False,
                )
            issued_token = generate_browser_token()
            digest = browser_token_sha256(issued_token)
            _lock_digest(digest)

        cart = _locked_current_cart(
            event=authoritative_event,
            digest=digest,
            now=current_time,
            return_expired=True,
        )
        order_state = _locked_origin_order_state(event=authoritative_event, digest=digest)
        if cart is not None and cart.expires_at <= current_time:
            if order_state.mutation_locked:
                snapshot = _raw_locked_snapshot(
                    cart=cart,
                    event=authoritative_event,
                    order_state=order_state,
                )
                return _mutation_result(
                    snapshot=snapshot,
                    selected=photo_id in snapshot.photo_ids,
                )
            _supersede_for_mutation(order_state)
            cart.delete()
            cart = None
        if cart is None and not selected:
            return _mutation_result(
                snapshot=_snapshot(event=authoritative_event),
                selected=False,
            )
        if cart is None and order_state.mutation_locked:
            return _mutation_result(
                snapshot=_snapshot(
                    event=authoritative_event,
                    mutation_locked=True,
                    pending_order_public_number=_pending_order_public_number(order_state),
                ),
                selected=False,
            )
        if cart is None:
            cart = Cart.objects.create(
                browser_token_sha256=digest,
                event=authoritative_event,
                expires_at=current_time + CART_TTL,
            )

        snapshot = _pruned_snapshot(
            cart=cart,
            event=authoritative_event,
            digest=digest,
            watermarked_previews_enabled=watermarked_previews_enabled,
            now=current_time,
            order_state=order_state,
        )
        if snapshot.mutation_locked:
            return _mutation_result(
                snapshot=snapshot,
                selected=photo_id in snapshot.photo_ids,
            )
        if not Cart.objects.filter(pk=cart.pk).exists():
            if not selected:
                return _mutation_result(snapshot=snapshot, selected=False)
            cart = Cart.objects.create(
                browser_token_sha256=digest,
                event=authoritative_event,
                expires_at=current_time + CART_TTL,
            )

        item = CartItem.objects.filter(cart=cart, photo_id=photo_id).first()
        if selected:
            if item is not None:
                return _mutation_result(snapshot=snapshot, selected=True)
            _supersede_for_mutation(order_state)
            CartItem.objects.create(cart=cart, photo_id=photo_id)
        else:
            if item is None:
                return _mutation_result(snapshot=snapshot, selected=False)
            _supersede_for_mutation(order_state)
            item.delete()

        if not CartItem.objects.filter(cart=cart).exists():
            cart.delete()
            updated_snapshot = _snapshot(
                event=authoritative_event,
                delete_browser_token=_should_delete_browser_token(digest=digest, now=current_time),
            )
        else:
            cart.expires_at = current_time + CART_TTL
            cart.save(update_fields=["expires_at"])
            updated_snapshot = _pruned_snapshot(
                cart=cart,
                event=authoritative_event,
                digest=digest,
                watermarked_previews_enabled=watermarked_previews_enabled,
                now=current_time,
                order_state=order_state,
            )
        return CartMutationResult(
            snapshot=updated_snapshot,
            selected=selected,
            changed=True,
            issued_browser_token=issued_token,
            refresh_browser_token=True,
            delete_browser_token=updated_snapshot.delete_browser_token,
        )


def clear_cart(
    *,
    event: Event,
    browser_token: str | None,
    now: datetime | None = None,
) -> CartMutationResult:
    """Delete an event selection without touching carts for other events."""
    current_time = now or timezone.now()
    digest = _digest_or_none(browser_token)
    with transaction.atomic():
        if digest is not None:
            _lock_digest(digest)
        authoritative_event = _locked_event(event)
        if digest is None:
            return _mutation_result(snapshot=_snapshot(event=authoritative_event), selected=False)
        cart = _locked_current_cart(
            event=authoritative_event,
            digest=digest,
            now=current_time,
            return_expired=True,
        )
        if cart is None:
            return _mutation_result(snapshot=_snapshot(event=authoritative_event), selected=False)
        order_state = _locked_origin_order_state(event=authoritative_event, digest=digest)
        if order_state.mutation_locked:
            return _mutation_result(
                snapshot=_raw_locked_snapshot(
                    cart=cart,
                    event=authoritative_event,
                    order_state=order_state,
                ),
                selected=False,
            )
        _supersede_for_mutation(order_state)
        cart.delete()
        snapshot = _snapshot(
            event=authoritative_event,
            delete_browser_token=_should_delete_browser_token(digest=digest, now=current_time),
        )
        return CartMutationResult(
            snapshot=snapshot,
            selected=False,
            changed=True,
            issued_browser_token=None,
            refresh_browser_token=True,
            delete_browser_token=snapshot.delete_browser_token,
        )


def _digest_or_none(token: str | None) -> str | None:
    if token is None:
        return None
    try:
        return browser_token_sha256(token)
    except ValueError:
        return None


def _locked_event(event: Event) -> Event:
    return Event.objects.select_for_update().get(pk=event.pk)


def _lock_digest(digest: str) -> None:
    lock_key = int.from_bytes(bytes.fromhex(digest)[:8], byteorder="big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_key])


def _locked_current_cart(
    *,
    event: Event,
    digest: str,
    now: datetime,
    discard_expired: bool = False,
    return_expired: bool = False,
) -> Cart | None:
    cart = (
        Cart.objects.select_for_update()
        .filter(
            event=event,
            browser_token_sha256=digest,
        )
        .first()
    )
    if cart is None:
        return None
    if cart.expires_at <= now:
        if return_expired:
            return cart
        if discard_expired:
            cart.delete()
        return None
    return cart


def _pruned_snapshot(
    *,
    cart: Cart,
    event: Event,
    digest: str,
    watermarked_previews_enabled: bool,
    now: datetime,
    order_state: _OriginOrderState | None = None,
) -> CartSnapshot:
    state = order_state or _locked_origin_order_state(event=event, digest=digest)
    if state.mutation_locked:
        return _raw_locked_snapshot(cart=cart, event=event, order_state=state)
    eligible = purchasable_paid_photo_queryset(
        event=event,
        watermarked_previews_enabled=watermarked_previews_enabled,
    )
    removed, _ = CartItem.objects.filter(cart=cart).exclude(photo_id__in=eligible).delete()
    if removed:
        _supersede_for_mutation(state)
    photo_ids = tuple(
        CartItem.objects.filter(cart=cart)
        .order_by("added_at", "photo_id")
        .values_list("photo_id", flat=True)
    )
    if not photo_ids:
        cart.delete()
        return _snapshot(
            event=event,
            pruned=removed > 0,
            delete_browser_token=_should_delete_browser_token(digest=digest, now=now),
        )
    return _snapshot(event=event, photo_ids=photo_ids, pruned=removed > 0)


def _snapshot(
    *,
    event: Event,
    photo_ids: tuple[str, ...] = (),
    pruned: bool = False,
    delete_browser_token: bool = False,
    mutation_locked: bool = False,
    pending_order_public_number: str | None = None,
) -> CartSnapshot:
    pricing = calculate_cart_pricing(event=event, item_count=len(photo_ids))
    return CartSnapshot(
        photo_ids=photo_ids,
        unit_price_kopecks=pricing.unit_price_kopecks,
        item_count=pricing.item_count,
        total_kopecks=pricing.total_kopecks,
        pruned=pruned,
        delete_browser_token=delete_browser_token,
        mutation_locked=mutation_locked,
        pending_order_public_number=pending_order_public_number,
    )


def _raw_locked_snapshot(
    *,
    cart: Cart,
    event: Event,
    order_state: _OriginOrderState,
) -> CartSnapshot:
    photo_ids = tuple(
        CartItem.objects.filter(cart=cart)
        .order_by("added_at", "photo_id")
        .values_list("photo_id", flat=True)
    )
    return _snapshot(
        event=event,
        photo_ids=photo_ids,
        mutation_locked=True,
        pending_order_public_number=_pending_order_public_number(order_state),
    )


def _pending_order_public_number(order_state: _OriginOrderState) -> str | None:
    return order_state.order.public_number if order_state.order is not None else None


def _locked_origin_order_state(*, event: Event, digest: str) -> _OriginOrderState:
    order = (
        Order.objects.select_for_update()
        .filter(
            event=event,
            originating_cart_token_sha256=digest,
            status=Order.Status.PENDING,
        )
        .first()
    )
    if order is None:
        return _OriginOrderState()
    attempts = list(
        PaymentAttempt.objects.select_for_update()
        .filter(order=order)
        .order_by("-created_at", "-pk")
    )
    if not attempts or any(
        attempt.status in (PaymentAttempt.Status.PENDING, PaymentAttempt.Status.SUCCEEDED)
        for attempt in attempts
    ):
        return _OriginOrderState(order=order, mutation_locked=True)
    return _OriginOrderState(
        order=order,
        supersede_on_mutation=attempts[0].status
        in (
            PaymentAttempt.Status.CANCELED,
            PaymentAttempt.Status.EXPIRED,
            PaymentAttempt.Status.FAILED,
            PaymentAttempt.Status.CONFLICT,
        ),
    )


def _supersede_for_mutation(order_state: _OriginOrderState) -> None:
    order = order_state.order
    if (
        not order_state.supersede_on_mutation
        or order is None
        or order.status != Order.Status.PENDING
    ):
        return
    order.status = Order.Status.SUPERSEDED
    order.save(update_fields=["status"])


def _mutation_result(*, snapshot: CartSnapshot, selected: bool) -> CartMutationResult:
    return CartMutationResult(
        snapshot=snapshot,
        selected=selected,
        changed=False,
        issued_browser_token=None,
        refresh_browser_token=False,
        delete_browser_token=snapshot.delete_browser_token,
    )


def _should_delete_browser_token(*, digest: str, now: datetime) -> bool:
    return not Cart.objects.filter(
        browser_token_sha256=digest,
        expires_at__gt=now,
    ).exists()
