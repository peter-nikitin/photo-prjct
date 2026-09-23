"""T-Bank eacq protocol; no method mutates Commerce state or retries Init.

Protocol source: https://developer.tbank.ru/schemas/eacq/openapi.yaml.
Only a merchant-approved, full-payment receipt without a closing obligation is supported.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.urls import reverse

from commerce.models import PaymentAttempt
from commerce.payment_gateway import (
    CreatedPayment,
    IncomingPaymentNotification,
    NormalizedPaymentStatus,
    PaymentGatewayError,
    PaymentGatewayErrorCategory,
    PaymentObservation,
    PaymentRequest,
)

TBANK_ADAPTER_KEY = "tbank-eacq-v1"
MAX_BODY_BYTES = 65536
_API_ORIGINS = {"https://securepay.tinkoff.ru", "https://rest-api-test.tinkoff.ru"}
_PAYMENT_HOSTS = {"pay.tbank.ru", "securepay.tinkoff.ru", "rest-api-test.tinkoff.ru"}
_OBJECTS_105 = frozenset(
    (
        "commodity excise job service gambling_bet gambling_prize lottery lottery_prize "
        "intellectual_activity payment agent_commission composite another"
    ).split()
)
_OBJECTS_12 = (_OBJECTS_105 - {"composite"}) | frozenset(
    (
        "contribution property_rights unrealization tax_reduction trade_fee resort_tax pledge "
        "income_decrease ie_pension_insurance_without_payments ie_pension_insurance_with_payments "
        "ie_medical_insurance_without_payments ie_medical_insurance_with_payments social_insurance "
        "casino_chips agent_payment excisable_goods_without_marking_code "
        "excisable_goods_with_marking_code goods_without_marking_code goods_with_marking_code"
    ).split()
)


class TBankAuthenticatedNotificationError(PaymentGatewayError):
    """Authenticated bank evidence conflicts with a known attempt; safe for attention."""

    def __init__(self, attempt_id: int) -> None:
        super().__init__(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        self.attempt_id = attempt_id


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Redirects must not forward terminal credentials to another endpoint.
urlopen = build_opener(_NoRedirect()).open


def _https_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return bool(
            url.scheme == "https"
            and url.hostname
            and not url.username
            and not url.password
            and url.port in (None, 443)
            and not url.fragment
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class TBankConfig:
    terminal_key: str = field(repr=False)
    terminal_password: str = field(repr=False)
    api_origin: str
    public_origin: str
    notification_url: str
    rub_only: bool
    pay_type: str
    receipt_ffd: str
    receipt_taxation: str
    receipt_tax: str
    receipt_payment_method: str
    receipt_payment_object: str
    receipt_measurement_unit: str
    receipt_closing_required: bool

    def __post_init__(self) -> None:
        if not self.terminal_key or len(self.terminal_key) > 64 or not self.terminal_password:
            raise ValueError("T-Bank terminal credentials are required.")
        if self.api_origin not in _API_ORIGINS or self.rub_only is not True or self.pay_type != "O":
            raise ValueError("T-Bank requires an approved API origin and one-stage RUB terminal.")
        if (
            not _https_url(self.public_origin)
            or urlsplit(self.public_origin).path
            or urlsplit(self.public_origin).query
            or not _https_url(self.notification_url)
            or urlsplit(self.notification_url).netloc != urlsplit(self.public_origin).netloc
            or urlsplit(self.notification_url).query
        ):
            raise ValueError("T-Bank requires public HTTPS return and notification URLs.")
        if (
            self.receipt_ffd not in {"1.05", "1.2"}
            or self.receipt_taxation
            not in {"osn", "usn_income", "usn_income_outcome", "esn", "patent"}
            or self.receipt_tax
            not in {
                "none",
                "vat0",
                "vat5",
                "vat7",
                "vat10",
                "vat22",
                "vat105",
                "vat107",
                "vat110",
                "vat122",
            }
            or self.receipt_payment_method != "full_payment"
            or self.receipt_payment_object
            not in (_OBJECTS_105 if self.receipt_ffd == "1.05" else _OBJECTS_12)
            or self.receipt_closing_required is not False
            or (self.receipt_ffd == "1.2" and self.receipt_measurement_unit != "шт")
        ):
            raise ValueError(
                "T-Bank requires a complete supported merchant-approved receipt profile."
            )


def sign_payload(payload: Mapping[str, object], password: str) -> str:
    values = {
        key: value
        for key, value in payload.items()
        if key not in {"Token", "Password"}
        and value is not None
        and not isinstance(value, (dict, list))
    }
    values["Password"] = password
    parts = []
    for key in sorted(values):
        value = values[key]
        if isinstance(value, bool):
            parts.append("true" if value else "false")
        elif isinstance(value, (str, int, float)):
            parts.append(str(value))
        else:
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
    try:
        message = "".join(parts).encode("utf-8")
    except UnicodeEncodeError:
        raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE) from None
    return hashlib.sha256(message).hexdigest()


def bank_order_id(idempotency_key: str) -> str:
    if not idempotency_key or len(idempotency_key) > 47:
        raise ValueError("T-Bank attempt identity exceeds the bank OrderId limit.")
    return "fm-" + idempotency_key


def normalize_status(status: object) -> NormalizedPaymentStatus:
    if status == "CONFIRMED":
        return NormalizedPaymentStatus.SUCCEEDED
    if status in (
        "NEW",
        "FORM_SHOWED",
        "AUTHORIZING",
        "AUTHORIZED",
        "3DS_CHECKING",
        "3DS_CHECKED",
        "CONFIRMING",
        "CONFIRM_CHECKING",
        "PAY_CHECKING",
        "PREAUTHORIZING",
    ):
        return NormalizedPaymentStatus.PENDING
    if status in ("AUTH_FAIL", "REJECTED"):
        return NormalizedPaymentStatus.FAILED
    if status == "CANCELED":
        return NormalizedPaymentStatus.CANCELED
    if status in ("DEADLINE_EXPIRED", "ATTEMPTS_EXPIRED"):
        return NormalizedPaymentStatus.EXPIRED
    raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _decode(body: bytes) -> dict[str, object]:
    try:
        if len(body) > MAX_BODY_BYTES:
            raise ValueError
        data = json.loads(body, object_pairs_hook=_unique_object)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, UnicodeError, RecursionError):
        raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE) from None


def _payment_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 20:
        raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
    return value


class TBankGateway:
    adapter_key = TBANK_ADAPTER_KEY

    def __init__(self, config: TBankConfig) -> None:
        self.config = config

    def _post(self, method: str, values: dict[str, object]) -> dict[str, object]:
        payload = {"TerminalKey": self.config.terminal_key, **values}
        payload["Token"] = sign_payload(payload, self.config.terminal_password)
        request = Request(
            self.config.api_origin + "/v2/" + method,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                if response.status != 200:
                    raise PaymentGatewayError(PaymentGatewayErrorCategory.UNAVAILABLE)
                data = _decode(response.read(MAX_BODY_BYTES + 1))
        except HTTPError as exc:
            category = (
                PaymentGatewayErrorCategory.AUTHENTICATION_FAILED
                if exc.code in (401, 403)
                else PaymentGatewayErrorCategory.UNAVAILABLE
            )
            raise PaymentGatewayError(category) from None
        except (URLError, OSError, TimeoutError, HTTPException):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.UNAVAILABLE) from None
        if data.get("Success") is not True or data.get("ErrorCode") != "0":
            # No documented bank error establishes that an uncertain Init created no operation.
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        if data.get("TerminalKey") != self.config.terminal_key:
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        return data

    def create_payment(self, request: PaymentRequest) -> CreatedPayment:
        expected_return = self.config.public_origin + reverse(
            "commerce:order_return", kwargs={"public_number": request.order_public_number}
        )
        if request.return_url != expected_return:
            raise ValueError("T-Bank return URL must be the public Order return route.")
        if len(request.receipt_lines) > 100 or len(request.checkout_email) > 64:
            raise ValueError("T-Bank receipt exceeds supported bank limits.")
        try:
            validate_email(request.checkout_email)
        except ValidationError:
            raise ValueError("T-Bank receipt requires a valid customer email.") from None
        items = []
        for line in request.receipt_lines:
            if len(line.description) > 128:
                raise ValueError("T-Bank receipt item name exceeds the bank limit.")
            item: dict[str, object] = {
                "Name": line.description,
                "Price": line.unit_amount_kopecks,
                "Quantity": 1,
                "Amount": line.line_total_kopecks,
                "Tax": self.config.receipt_tax,
                "PaymentMethod": self.config.receipt_payment_method,
                "PaymentObject": self.config.receipt_payment_object,
            }
            if self.config.receipt_ffd == "1.2":
                item["MeasurementUnit"] = self.config.receipt_measurement_unit
            items.append(item)
        order_id = bank_order_id(request.idempotency_key)
        data = self._post(
            "Init",
            {
                "Amount": request.amount_kopecks,
                "OrderId": order_id,
                "PayType": "O",
                "Description": "FindMe Photo",
                "SuccessURL": request.return_url,
                "FailURL": request.return_url,
                "NotificationURL": self.config.notification_url,
                "Receipt": {
                    "Email": request.checkout_email,
                    "Taxation": self.config.receipt_taxation,
                    "Items": items,
                },
            },
        )
        if (
            data.get("OrderId") != order_id
            or type(data.get("Amount")) is not int
            or data["Amount"] != request.amount_kopecks
            or data.get("Status") != "NEW"
        ):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        url = data.get("PaymentURL")
        if (
            not isinstance(url, str)
            or len(url) > 2000
            or not _https_url(url)
            or urlsplit(url).hostname not in _PAYMENT_HOSTS
        ):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        return CreatedPayment(
            provider_payment_id=_payment_id(data.get("PaymentId")),
            status=NormalizedPaymentStatus.PENDING,
            amount_kopecks=request.amount_kopecks,
            currency="RUB",
            confirmation_url=url,
            expires_at=None,
        )

    def _observation(self, data: dict[str, object], attempt: PaymentAttempt) -> PaymentObservation:
        payment_id = _payment_id(data.get("PaymentId"))
        if (
            data.get("TerminalKey") != self.config.terminal_key
            or data.get("OrderId") != bank_order_id(attempt.idempotency_key)
            or (attempt.provider_payment_id and payment_id != attempt.provider_payment_id)
            or type(data.get("Amount")) is not int
            or data["Amount"] != attempt.amount_kopecks
            or attempt.currency != "RUB"
        ):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        status = normalize_status(data.get("Status"))
        if type(data.get("Success")) is not bool or (
            status == NormalizedPaymentStatus.SUCCEEDED
            and (data["Success"] is not True or data.get("ErrorCode") != "0")
        ):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        return PaymentObservation(
            provider_payment_id=payment_id,
            status=status,
            amount_kopecks=attempt.amount_kopecks,
            currency="RUB",
            idempotency_key=attempt.idempotency_key,
        )

    def fetch_payment(self, provider_payment_id: str) -> PaymentObservation:
        attempt = PaymentAttempt.objects.filter(
            adapter_key=self.adapter_key, provider_payment_id=_payment_id(provider_payment_id)
        ).first()
        if attempt is None:
            raise PaymentGatewayError(PaymentGatewayErrorCategory.NOT_FOUND)
        return self._observation(
            self._post("GetState", {"PaymentId": provider_payment_id}), attempt
        )

    def recover_payment(self, idempotency_key: str) -> PaymentObservation | None:
        """Return bank evidence, or None for an inconclusive empty lookup; never retry Init."""
        attempt = PaymentAttempt.objects.filter(
            adapter_key=self.adapter_key, idempotency_key=idempotency_key
        ).first()
        if attempt is None:
            raise PaymentGatewayError(PaymentGatewayErrorCategory.NOT_FOUND)
        order_id = bank_order_id(idempotency_key)
        data = self._post("CheckOrder", {"OrderId": order_id})
        payments = data.get("Payments")
        if data.get("OrderId") != order_id or not isinstance(payments, list) or len(payments) > 1:
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        if not payments:
            return None
        if not isinstance(payments[0], dict):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        payment_id = _payment_id(payments[0].get("PaymentId"))
        state = self._post("GetState", {"PaymentId": payment_id})
        if state.get("PaymentId") != payment_id:
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        return self._observation(state, attempt)

    def authenticate_notification(
        self, notification: IncomingPaymentNotification
    ) -> PaymentObservation:
        data = _decode(notification.body)
        token = data.get("Token")
        if (
            not isinstance(token, str)
            or len(token) != 64
            or not token.isascii()
            or not hmac.compare_digest(token, sign_payload(data, self.config.terminal_password))
        ):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.AUTHENTICATION_FAILED)
        if data.get("TerminalKey") != self.config.terminal_key:
            raise PaymentGatewayError(PaymentGatewayErrorCategory.AUTHENTICATION_FAILED)
        order_id = data.get("OrderId")
        if not isinstance(order_id, str) or not order_id.startswith("fm-") or len(order_id) > 50:
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        attempt = PaymentAttempt.objects.filter(
            adapter_key=self.adapter_key, idempotency_key=order_id[3:]
        ).first()
        if attempt is None:
            raise PaymentGatewayError(PaymentGatewayErrorCategory.NOT_FOUND)
        try:
            return self._observation(data, attempt)
        except PaymentGatewayError:
            raise TBankAuthenticatedNotificationError(attempt.pk) from None


def tbank_gateway_factory() -> TBankGateway:
    origin = str(getattr(settings, "COMMERCE_PUBLIC_ORIGIN", "")).rstrip("/")
    values = {
        name: str(getattr(settings, "TBANK_" + name.upper(), ""))
        for name in (
            "terminal_key",
            "terminal_password",
            "api_origin",
            "pay_type",
            "receipt_ffd",
            "receipt_taxation",
            "receipt_tax",
            "receipt_payment_method",
            "receipt_payment_object",
            "receipt_measurement_unit",
        )
    }
    return TBankGateway(
        TBankConfig(
            rub_only=getattr(settings, "TBANK_RUB_ONLY", None) is True,
            receipt_closing_required=(
                getattr(settings, "TBANK_RECEIPT_CLOSING_REQUIRED", None) is not False
            ),
            public_origin=origin,
            notification_url=origin + reverse("payment_notification"),
            **values,
        )
    )
