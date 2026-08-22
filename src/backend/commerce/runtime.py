from urllib.parse import urlsplit

from django.conf import settings
from django.urls import reverse
from django.utils.module_loading import import_string

from commerce.worker import CommerceWorker


def commerce_worker_factory() -> CommerceWorker:
    """Assemble the configured narrow adapters into the existing Commerce worker."""
    origin = _public_origin()
    payment_gateway = _configured_adapter("COMMERCE_PAYMENT_GATEWAY_FACTORY")
    email_sender = _configured_adapter("COMMERCE_EMAIL_SENDER_FACTORY")
    signing_secret = getattr(settings, "COMMERCE_ORDER_ACCESS_SIGNING_SECRET", "")
    support_contact = getattr(settings, "COMMERCE_SUPPORT_CONTACT", "")
    if not isinstance(signing_secret, (str, bytes)) or not signing_secret:
        raise ValueError("Commerce signing secret is required.")
    if not isinstance(support_contact, str) or not support_contact:
        raise ValueError("Commerce support contact is required.")
    return CommerceWorker(
        email_sender=email_sender,
        payment_gateway=payment_gateway,
        order_access_signing_secret=signing_secret,
        order_access_url_for_grant=lambda grant, signature: (
            origin
            + reverse(
                "commerce:grant_order",
                kwargs={
                    "public_number": grant.order.public_number,
                    "grant_identifier": grant.pk,
                    "signature": signature,
                },
            )
        ),
        support_contact=support_contact,
        admin_url_for_attention=lambda attention: (
            origin
            + reverse(
                "admin:commerce_commerceattention_change",
                args=(attention.pk,),
            )
        ),
    )


def _configured_adapter(setting_name: str):
    path = getattr(settings, setting_name, "")
    if not isinstance(path, str) or not path:
        raise ValueError(f"{setting_name} is required.")
    return import_string(path)()


def _public_origin() -> str:
    origin = getattr(settings, "COMMERCE_PUBLIC_ORIGIN", "")
    if not isinstance(origin, str) or not origin or origin.endswith("/"):
        raise ValueError("Commerce public origin is required without a trailing slash.")
    parsed = urlsplit(origin)
    if parsed.scheme == "https" and parsed.netloc and not parsed.path:
        return origin
    if (
        settings.DEBUG is True
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
        and parsed.netloc
        and not parsed.path
    ):
        return origin
    raise ValueError("Commerce public origin must use HTTPS outside local debug.")
