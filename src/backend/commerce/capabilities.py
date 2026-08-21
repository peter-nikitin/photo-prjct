import base64
import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from django.db.models import Max
from django.utils import timezone

from commerce.identity import browser_token_sha256, generate_browser_token, parse_browser_token
from commerce.models import Order, OrderAccessGrant

_PURCHASE_BROWSER_LIFETIME = timedelta(days=30)
_ORDER_ACCESS_SIGNATURE_CONTEXT = b"findme-photo-order-access-v1\0"


@dataclass(frozen=True)
class PurchaseBrowserCapability:
    token: str = field(repr=False)
    token_sha256: str
    expires_at: datetime


def _encode_opaque_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _valid_purchase_browser_token(token: object) -> str | None:
    if not isinstance(token, str) or not token:
        return None
    try:
        parse_browser_token(token)
    except ValueError:
        return None
    return token


def issue_purchase_browser_capability(
    *,
    order_created_at: datetime | None = None,
    existing_token: str | None = None,
) -> PurchaseBrowserCapability:
    """Issue or creation-time refresh the browser bearer for a newly created Order."""
    created_at = order_created_at or timezone.now()
    token = _valid_purchase_browser_token(existing_token) or generate_browser_token()
    return PurchaseBrowserCapability(
        token=token,
        token_sha256=browser_token_sha256(token),
        expires_at=created_at + _PURCHASE_BROWSER_LIFETIME,
    )


def purchase_browser_authorizes_order(
    *,
    order: Order,
    token: object,
    now: datetime | None = None,
) -> bool:
    """Return one sanitized authorization result without mutating cookie or Order state."""
    valid_token = _valid_purchase_browser_token(token)
    if valid_token is None or order.pk is None:
        return False
    stored_digest = order.purchase_browser_token_sha256
    if not isinstance(stored_digest, str) or len(stored_digest) != 64:
        return False
    if not hmac.compare_digest(browser_token_sha256(valid_token), stored_digest):
        return False
    matching_orders = Order.objects.filter(purchase_browser_token_sha256=stored_digest)
    if not matching_orders.filter(pk=order.pk).exists():
        return False
    latest_order_created_at = matching_orders.aggregate(latest=Max("created_at"))["latest"]
    if latest_order_created_at is None:
        return False
    checked_at = now or timezone.now()
    return checked_at < latest_order_created_at + _PURCHASE_BROWSER_LIFETIME


def create_order_access_grant(
    *,
    order: Order,
    source: str,
    created_by=None,
) -> OrderAccessGrant:
    if source == OrderAccessGrant.Source.ADMIN and created_by is None:
        raise ValueError("Administrator grant creation requires an actor.")
    return OrderAccessGrant.objects.create(
        order=order,
        source=source,
        created_by=created_by,
    )


def _order_access_signature_payload(grant: OrderAccessGrant) -> bytes:
    return (
        _ORDER_ACCESS_SIGNATURE_CONTEXT
        + str(grant.order_id).encode("ascii")
        + b":"
        + grant.pk.hex.encode("ascii")
    )


def _signing_secret_bytes(signing_secret: str | bytes) -> bytes:
    if isinstance(signing_secret, str):
        secret = signing_secret.encode("utf-8")
    elif isinstance(signing_secret, bytes):
        secret = signing_secret
    else:
        raise ValueError("Order access signing secret must be configured.")
    if not secret:
        raise ValueError("Order access signing secret must be configured.")
    return secret


def sign_order_access_grant(
    *,
    grant: OrderAccessGrant,
    signing_secret: str | bytes,
) -> str:
    digest = hmac.new(
        _signing_secret_bytes(signing_secret),
        _order_access_signature_payload(grant),
        hashlib.sha256,
    ).digest()
    return _encode_opaque_bytes(digest)


def verify_order_access_grant(
    *,
    order: Order,
    grant_identifier: object,
    signature: object,
    signing_secret: str | bytes,
) -> OrderAccessGrant | None:
    """Resolve an active exact-order grant, returning None for every invalid bearer."""
    if not isinstance(signature, str) or not signature:
        return None
    try:
        grant_id = UUID(str(grant_identifier))
    except (AttributeError, TypeError, ValueError):
        return None
    grant = OrderAccessGrant.objects.filter(
        pk=grant_id,
        order_id=order.pk,
        revoked_at__isnull=True,
    ).first()
    if grant is None:
        return None
    expected = sign_order_access_grant(
        grant=grant,
        signing_secret=signing_secret,
    )
    if not hmac.compare_digest(expected, signature):
        return None
    return grant


def revoke_order_access_grant(
    grant: OrderAccessGrant,
    *,
    revoked_at: datetime | None = None,
) -> None:
    if grant.revoked_at is not None:
        return
    grant.revoked_at = revoked_at or timezone.now()
    grant.save(update_fields=["revoked_at"])
