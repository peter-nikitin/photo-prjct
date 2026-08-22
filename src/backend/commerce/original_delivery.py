from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction
from django.utils import timezone
from ingestion.storage import ObjectChanged, ObjectMismatch, ObjectMissing, StorageUnavailable
from picflow.gallery import FinalObjectStorage

from commerce.attention import open_attention
from commerce.capabilities import purchase_browser_authorizes_order, verify_order_access_grant
from commerce.models import DownloadGrantAudit, Order, OrderAccessGrant, OrderItem


class PurchasedOriginalDenied(Exception):
    """A sanitized denial before an original is eligible for delivery."""

    def __init__(self) -> None:
        super().__init__("Purchased original is unavailable.")


class PurchasedOriginalUnavailable(Exception):
    """A sanitized failure after an authorized exact-object delivery attempt."""

    def __init__(self) -> None:
        super().__init__("Purchased original is temporarily unavailable.")


@dataclass(frozen=True)
class PurchasedOriginalDownload:
    signed_url: str = field(repr=False)


@dataclass(frozen=True)
class _Authorization:
    source: str
    access_grant: OrderAccessGrant | None


@dataclass(frozen=True)
class _ExactOriginalMissing(Exception):
    order_id: int
    order_item_id: int


class _ExactOriginalUnavailable(Exception):
    pass


def sign_purchased_original(
    *,
    order: Order,
    photo_id: object,
    purchase_browser_token: object,
    grant_identifier: object,
    grant_signature: object,
    order_access_signing_secret: str | bytes | None,
    storage: FinalObjectStorage,
    now: datetime | None = None,
) -> PurchasedOriginalDownload:
    """Authorize one paid OrderItem, then sign exactly its retained private original."""
    if order.pk is None or not isinstance(photo_id, str) or not photo_id:
        raise PurchasedOriginalDenied()
    current_time = now or timezone.now()

    try:
        with transaction.atomic():
            current_order = Order.objects.select_for_update().filter(pk=order.pk).first()
            if current_order is None:
                raise PurchasedOriginalDenied()
            authorization = _authorize_order(
                order=current_order,
                purchase_browser_token=purchase_browser_token,
                grant_identifier=grant_identifier,
                grant_signature=grant_signature,
                order_access_signing_secret=order_access_signing_secret,
                now=current_time,
            )
            if current_order.status != Order.Status.PAID:
                raise PurchasedOriginalDenied()

            order_item = (
                OrderItem.objects.select_related("photo")
                .filter(order=current_order, photo_id=photo_id)
                .first()
            )
            if order_item is None:
                raise PurchasedOriginalDenied()

            signed_url = _sign_exact_original(order_item=order_item, storage=storage)
            DownloadGrantAudit.objects.create(
                order_item=order_item,
                authorization_source=authorization.source,
                access_grant=authorization.access_grant,
            )
            if current_order.first_customer_access_at is None:
                current_order.first_customer_access_at = current_time
                current_order.save(update_fields=["first_customer_access_at"])
    except _ExactOriginalMissing as failure:
        _record_missing_original(
            order_id=failure.order_id,
            order_item_id=failure.order_item_id,
            now=current_time,
        )
        raise PurchasedOriginalUnavailable() from None
    except _ExactOriginalUnavailable:
        raise PurchasedOriginalUnavailable() from None
    return PurchasedOriginalDownload(signed_url=signed_url)


def _authorize_order(
    *,
    order: Order,
    purchase_browser_token: object,
    grant_identifier: object,
    grant_signature: object,
    order_access_signing_secret: str | bytes | None,
    now: datetime,
) -> _Authorization:
    if purchase_browser_authorizes_order(
        order=order,
        token=purchase_browser_token,
        now=now,
    ):
        return _Authorization(
            source=str(DownloadGrantAudit.AuthorizationSource.PURCHASE_BROWSER),
            access_grant=None,
        )

    if not isinstance(order_access_signing_secret, (str, bytes)):
        raise PurchasedOriginalDenied()
    try:
        access_grant = verify_order_access_grant(
            order=order,
            grant_identifier=grant_identifier,
            signature=grant_signature,
            signing_secret=order_access_signing_secret,
        )
    except ValueError:
        access_grant = None
    if access_grant is None:
        raise PurchasedOriginalDenied()
    return _Authorization(
        source=str(DownloadGrantAudit.AuthorizationSource.ORDER_ACCESS_GRANT),
        access_grant=access_grant,
    )


def _sign_exact_original(
    *,
    order_item: OrderItem,
    storage: FinalObjectStorage,
) -> str:
    photo = order_item.photo
    original_key = photo.original_key
    extension = {
        "image/jpeg": "jpg",
        "image/png": "png",
    }.get(photo.original_content_type or "")
    if not original_key or extension is None:
        raise _ExactOriginalMissing(order_item.order_id, order_item.pk)

    try:
        signed_url = storage.sign_final(
            key=original_key,
            attachment_filename=f"findme-photo-{photo.pk}.{extension}",
        )
        if not isinstance(signed_url, str) or not signed_url:
            raise ObjectMismatch()
    except (ObjectChanged, ObjectMismatch, ObjectMissing, ValueError):
        raise _ExactOriginalMissing(order_item.order_id, order_item.pk) from None
    except StorageUnavailable:
        raise _ExactOriginalUnavailable() from None
    return signed_url


def _record_missing_original(*, order_id: int, order_item_id: int, now: datetime) -> None:
    open_attention(
        kind="original_missing",
        subject=f"order-item:{order_item_id}",
        order=Order.objects.get(pk=order_id),
        now=now,
    )
