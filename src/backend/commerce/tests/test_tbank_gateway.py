import json
from dataclasses import replace
from email.message import Message
from http.client import IncompleteRead
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
from django.test import SimpleTestCase

from commerce.payment_gateway import (
    IncomingPaymentNotification,
    PaymentGatewayError,
    PaymentGatewayErrorCategory,
    PaymentReceiptLine,
    PaymentRequest,
)


def gateway_module():
    from commerce import tbank_gateway

    return tbank_gateway


def config(**changes):
    module = gateway_module()
    values = dict(
        terminal_key="terminal",
        terminal_password="secret",
        api_origin="https://securepay.tinkoff.ru",
        public_origin="https://photos.example",
        notification_url="https://photos.example/payments/notification/",
        rub_only=True,
        pay_type="O",
        receipt_ffd="1.05",
        receipt_taxation="usn_income",
        receipt_tax="none",
        receipt_payment_method="full_payment",
        receipt_payment_object="intellectual_activity",
        receipt_measurement_unit="",
        receipt_closing_required=False,
    )
    values.update(changes)
    return module.TBankConfig(**values)


def request():
    return PaymentRequest(
        order_public_number="order-1",
        amount_kopecks=1500,
        currency="RUB",
        receipt_lines=(
            PaymentReceiptLine("Photo 1", 1, 1000, 1000),
            PaymentReceiptLine("Photo 2", 1, 500, 500),
        ),
        checkout_email="buyer@example.test",
        idempotency_key="attempt-1",
        return_url="https://photos.example/orders/order-1/return/",
    )


def response(**changes):
    data = dict(
        TerminalKey="terminal",
        Amount=1500,
        OrderId="fm-attempt-1",
        Success=True,
        PaymentId="12345",
        ErrorCode="0",
        Status="NEW",
        PaymentURL="https://pay.tbank.ru/new/abc",
    )
    data.update(changes)
    return data


class Response:
    status = 200

    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, size):
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TBankSigningTests(SimpleTestCase):
    def test_official_init_token_includes_only_root_values(self):
        actual = gateway_module().sign_payload(
            dict(
                TerminalKey="MerchantTerminalKey",
                Amount=19200,
                OrderId="00000",
                Description="Подарочная карта на 1000 рублей",
                Receipt={"Email": "private"},
                DATA=[],
            ),
            "11111111111111",
        )
        self.assertEqual(actual, "72dd466f8ace0a37a1f740ce5fb78101712bc0665d91a8108c7c8a0ccd426db2")

    def test_official_notification_token_uses_lowercase_boolean(self):
        actual = gateway_module().sign_payload(
            dict(
                TerminalKey="1234567890DEMO",
                OrderId="000000",
                Success=True,
                Status="AUTHORIZED",
                PaymentId="0000000",
                ErrorCode="0",
                Amount="1111",
                CardId="000000",
                Pan="200000******0000",
                ExpDate="1111",
                RebillId="000000",
                Token="ignored",
                Data={},
                Optional=None,
            ),
            "11111111111",
        )
        self.assertEqual(actual, "1c0964277d0213349243065a0d5b838b8e90d2d25f740d0f2767836e710e80c8")

    def test_bank_order_identity_is_stable_and_unique_to_attempt(self):
        self.assertEqual(gateway_module().bank_order_id("attempt-1"), "fm-attempt-1")
        self.assertNotEqual(
            gateway_module().bank_order_id("attempt-1"), gateway_module().bank_order_id("attempt-2")
        )
        with self.assertRaises(ValueError):
            gateway_module().bank_order_id("x" * 48)


class TBankProtocolTests(SimpleTestCase):
    def test_init_sends_exact_receipt_and_signed_one_stage_request(self):
        module = gateway_module()
        with patch.object(module, "urlopen", return_value=Response(response())) as network:
            result = module.TBankGateway(config()).create_payment(request())
        sent = json.loads(network.call_args.args[0].data)
        self.assertEqual(sent["Amount"], 1500)
        self.assertEqual(sent["OrderId"], "fm-attempt-1")
        self.assertEqual(sent["PayType"], "O")
        self.assertEqual(sent["SuccessURL"], request().return_url)
        self.assertEqual(sent["FailURL"], request().return_url)
        self.assertEqual(
            sent["Receipt"],
            {
                "Email": "buyer@example.test",
                "Taxation": "usn_income",
                "Items": [
                    {
                        "Name": "Photo 1",
                        "Price": 1000,
                        "Quantity": 1,
                        "Amount": 1000,
                        "Tax": "none",
                        "PaymentMethod": "full_payment",
                        "PaymentObject": "intellectual_activity",
                    },
                    {
                        "Name": "Photo 2",
                        "Price": 500,
                        "Quantity": 1,
                        "Amount": 500,
                        "Tax": "none",
                        "PaymentMethod": "full_payment",
                        "PaymentObject": "intellectual_activity",
                    },
                ],
            },
        )
        self.assertEqual(sent["Token"], module.sign_payload(sent, "secret"))
        self.assertEqual(result.provider_payment_id, "12345")
        self.assertEqual(result.status, "pending")
        self.assertEqual(result.currency, "RUB")
        self.assertEqual(network.call_count, 1)
        self.assertLessEqual(network.call_args.kwargs["timeout"], 10)

    def test_ffd12_adds_explicit_measurement(self):
        module = gateway_module()
        with patch.object(module, "urlopen", return_value=Response(response())) as network:
            module.TBankGateway(
                config(receipt_ffd="1.2", receipt_measurement_unit="шт")
            ).create_payment(request())
        self.assertEqual(
            json.loads(network.call_args.args[0].data)["Receipt"]["Items"][0]["MeasurementUnit"],
            "шт",
        )

    def test_timeout_is_uncertain_safe_and_never_retried(self):
        module = gateway_module()
        with patch.object(
            module, "urlopen", side_effect=TimeoutError("secret buyer@example.test")
        ) as network:
            with self.assertRaises(PaymentGatewayError) as caught:
                module.TBankGateway(config()).create_payment(request())
        self.assertEqual(caught.exception.category, PaymentGatewayErrorCategory.UNAVAILABLE)
        self.assertEqual(str(caught.exception), "unavailable")
        self.assertEqual(network.call_count, 1)

    def test_invalid_return_or_receipt_is_rejected_before_network(self):
        module = gateway_module()
        bad_requests = [
            replace(request(), checkout_email="a" * 65),
            replace(request(), return_url="https://evil.example/order-1"),
            replace(request(), return_url=request().return_url + "?token=secret"),
            replace(request(), receipt_lines=(PaymentReceiptLine("x" * 129, 1, 1500, 1500),)),
            replace(
                request(),
                amount_kopecks=101,
                receipt_lines=(PaymentReceiptLine("Photo", 1, 1, 1),) * 101,
            ),
        ]
        with patch.object(module, "urlopen") as network:
            for item in bad_requests:
                with self.subTest(item=item), self.assertRaises(ValueError):
                    module.TBankGateway(config()).create_payment(item)
        network.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        dict(Amount=True),
        dict(Amount=1501),
        dict(TerminalKey="other"),
        dict(OrderId="other"),
        dict(PaymentId=""),
        dict(PaymentId=True),
        dict(Success="true"),
        dict(ErrorCode="1"),
        dict(Status="CONFIRMED"),
        dict(PaymentURL="http://pay.tbank.ru/a"),
        dict(PaymentURL="https://evil.example/a"),
        dict(PaymentURL="https://pay.tbank.ru.evil.example/a"),
    ],
)
def test_init_rejects_malformed_or_conflicting_response(changes):
    module = gateway_module()
    with patch.object(module, "urlopen", return_value=Response(response(**changes))):
        with pytest.raises(PaymentGatewayError):
            module.TBankGateway(config()).create_payment(request())


@pytest.mark.parametrize("body", [b"bad json", b"[]", b"{}", b"x" * 65537])
def test_response_parsing_is_bounded_and_fail_closed(body):
    module = gateway_module()
    with patch.object(module, "urlopen", return_value=Response(body)):
        with pytest.raises(PaymentGatewayError):
            module.TBankGateway(config()).create_payment(request())


@pytest.mark.parametrize(
    "changes",
    [
        dict(terminal_key=""),
        dict(terminal_password=""),
        dict(rub_only=False),
        dict(pay_type="T"),
        dict(api_origin="https://evil.example"),
        dict(public_origin="http://photos.example"),
        dict(receipt_ffd=""),
        dict(receipt_taxation=""),
        dict(receipt_tax=""),
        dict(receipt_payment_method="advance"),
        dict(receipt_payment_object=""),
        dict(receipt_closing_required=None),
        dict(receipt_closing_required=True),
        dict(receipt_ffd="1.2", receipt_measurement_unit=""),
    ],
)
def test_configuration_fails_closed(changes):
    with pytest.raises(ValueError):
        config(**changes)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("CONFIRMED", "succeeded"),
        ("AUTHORIZED", "pending"),
        ("NEW", "pending"),
        ("AUTHORIZING", "pending"),
        ("FORM_SHOWED", "pending"),
        ("CONFIRMING", "pending"),
        ("REJECTED", "failed"),
        ("AUTH_FAIL", "failed"),
        ("CANCELED", "canceled"),
        ("DEADLINE_EXPIRED", "expired"),
        ("ATTEMPTS_EXPIRED", "expired"),
    ],
)
def test_status_normalization(status, expected):
    assert gateway_module().normalize_status(status) == expected


@pytest.mark.parametrize(
    "status", ["REFUNDED", "PARTIAL_REFUNDED", "REVERSED", "REVERSING", "UNKNOWN"]
)
def test_unhandled_status_requires_attention(status):
    with pytest.raises(PaymentGatewayError):
        gateway_module().normalize_status(status)


@pytest.fixture
def attempt(db, request):
    from datetime import date

    from picflow.models import Event, Photo

    from commerce.models import Order, OrderItem, PaymentAttempt

    event = Event.objects.create(
        name="Bank event",
        slug="bank-event",
        start_date=date(2026, 9, 23),
        end_date=date(2026, 9, 23),
        city="Moscow",
    )
    order = Order.objects.create(
        public_number="FM-BANK2345",
        event=event,
        checkout_email="buyer@example.test",
        total_kopecks=1500,
        originating_cart_token_sha256="a" * 64,
        purchase_browser_token_sha256="b" * 64,
    )
    photo = Photo.objects.create(id="bank-photo", event=event, src="photos/bank.jpg")
    OrderItem.objects.create(
        order=order,
        photo=photo,
        photo_public_id=photo.pk,
        unit_price_kopecks=1500,
        line_total_kopecks=1500,
    )
    return PaymentAttempt.objects.create(
        order=order,
        amount_kopecks=1500,
        currency="RUB",
        adapter_key="tbank-eacq-v1",
        idempotency_key="attempt-1",
        provider_payment_id=getattr(request, "param", "12345"),
    )


def signed_notification(**changes):
    data = response(Status="CONFIRMED")
    data.pop("PaymentURL")
    data.update(changes)
    data["Token"] = gateway_module().sign_payload(data, "secret")
    return IncomingPaymentNotification(headers={}, body=json.dumps(data).encode())


def test_get_state_binds_signed_request_to_persisted_attempt(attempt):
    module = gateway_module()
    with patch.object(
        module, "urlopen", return_value=Response(response(Status="CONFIRMED"))
    ) as network:
        result = module.TBankGateway(config()).fetch_payment("12345")
    assert result.status == "succeeded"
    assert result.idempotency_key == "attempt-1"
    sent = json.loads(network.call_args.args[0].data)
    assert sent == {
        "TerminalKey": "terminal",
        "PaymentId": "12345",
        "Token": module.sign_payload({"TerminalKey": "terminal", "PaymentId": "12345"}, "secret"),
    }
    attempt.refresh_from_db()
    assert attempt.status == "pending"


def test_callback_resolves_persisted_attempt_without_mutating_it(attempt):
    result = (
        gateway_module().TBankGateway(config()).authenticate_notification(signed_notification())
    )
    assert result.status == "succeeded"
    assert result.idempotency_key == attempt.idempotency_key
    attempt.refresh_from_db()
    assert attempt.status == "pending"


@pytest.mark.parametrize(
    "changes",
    [
        dict(TerminalKey="wrong"),
        dict(PaymentId="67890"),
        dict(OrderId="fm-unknown"),
        dict(Amount=1501),
        dict(Amount=True),
        dict(Success=False),
        dict(ErrorCode="1"),
    ],
)
def test_valid_signature_does_not_authorize_wrong_identity_amount_or_failed_success(
    attempt, changes
):
    with pytest.raises(PaymentGatewayError):
        gateway_module().TBankGateway(config()).authenticate_notification(
            signed_notification(**changes)
        )


def test_callback_checks_unknown_scalar_fields_in_signature_before_database():
    notification = signed_notification(CardId="123", Extra="before", Data={"nested": 1})
    payload = json.loads(notification.body)
    payload["Extra"] = "after"
    with pytest.raises(PaymentGatewayError) as caught:
        gateway_module().TBankGateway(config()).authenticate_notification(
            IncomingPaymentNotification(headers={}, body=json.dumps(payload).encode())
        )
    assert caught.value.category == PaymentGatewayErrorCategory.AUTHENTICATION_FAILED


def test_callback_rejects_duplicate_json_keys():
    payload = signed_notification().body[:-1] + b', "Amount": 1500}'
    with pytest.raises(PaymentGatewayError) as caught:
        gateway_module().TBankGateway(config()).authenticate_notification(
            IncomingPaymentNotification(headers={}, body=payload)
        )
    assert caught.value.category == PaymentGatewayErrorCategory.INVALID_RESPONSE


@pytest.mark.parametrize("attempt", [""], indirect=True)
def test_recovery_uses_check_order_then_get_state_and_keeps_db_unchanged(attempt):
    module = gateway_module()
    check = {
        "TerminalKey": "terminal",
        "OrderId": "fm-attempt-1",
        "Success": True,
        "ErrorCode": "0",
        "Payments": [{"PaymentId": "12345", "Status": "CONFIRMED", "Success": "true"}],
    }
    with patch.object(
        module, "urlopen", side_effect=[Response(check), Response(response(Status="CONFIRMED"))]
    ) as network:
        result = module.TBankGateway(config()).recover_payment("attempt-1")
    assert result.status == "succeeded"
    assert result.provider_payment_id == "12345"
    assert [call.args[0].full_url.rsplit("/", 1)[-1] for call in network.call_args_list] == [
        "CheckOrder",
        "GetState",
    ]
    assert json.loads(network.call_args_list[0].args[0].data)["OrderId"] == "fm-attempt-1"
    attempt.refresh_from_db()
    assert attempt.provider_payment_id == ""


def test_empty_recovery_is_inconclusive_and_does_not_call_init(attempt):
    module = gateway_module()
    with patch.object(
        module,
        "urlopen",
        return_value=Response(
            {
                "TerminalKey": "terminal",
                "OrderId": "fm-attempt-1",
                "Success": True,
                "ErrorCode": "0",
                "Payments": [],
            }
        ),
    ) as network:
        assert module.TBankGateway(config()).recover_payment("attempt-1") is None
    assert network.call_count == 1


@pytest.mark.parametrize(
    "payments", [[{"PaymentId": "12345"}, {"PaymentId": "67890"}], None, [None]]
)
def test_recovery_rejects_ambiguous_operations(attempt, payments):
    module = gateway_module()
    with patch.object(
        module,
        "urlopen",
        return_value=Response(
            {
                "TerminalKey": "terminal",
                "OrderId": "fm-attempt-1",
                "Success": True,
                "ErrorCode": "0",
                "Payments": payments,
            }
        ),
    ):
        with pytest.raises(PaymentGatewayError):
            module.TBankGateway(config()).recover_payment("attempt-1")


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (URLError("private"), "unavailable"),
        (HTTPError("https://bank", 401, "private", Message(), None), "authentication_failed"),
        (HTTPError("https://bank", 500, "private", Message(), None), "unavailable"),
    ],
)
def test_http_failures_have_safe_categories(error, category):
    module = gateway_module()
    with patch.object(module, "urlopen", side_effect=error):
        with pytest.raises(PaymentGatewayError) as caught:
            module.TBankGateway(config()).create_payment(request())
    assert caught.value.category == category
    assert str(caught.value) == category


def test_factory_requires_explicit_complete_configuration(settings):
    module = gateway_module()
    settings.COMMERCE_PUBLIC_ORIGIN = "https://photos.example"
    for name, value in vars(config()).items():
        if name not in {"public_origin", "notification_url"}:
            setattr(settings, "TBANK_" + name.upper(), value)
    assert module.tbank_gateway_factory().adapter_key == "tbank-eacq-v1"
    settings.TBANK_RECEIPT_CLOSING_REQUIRED = ""
    with pytest.raises(ValueError):
        module.tbank_gateway_factory()


def test_truncated_transport_response_is_uncertain_and_safe():
    module = gateway_module()
    with patch.object(module, "urlopen", side_effect=IncompleteRead(b"secret buyer@example.test")):
        with pytest.raises(PaymentGatewayError) as caught:
            module.TBankGateway(config()).create_payment(request())
    assert caught.value.category == PaymentGatewayErrorCategory.UNAVAILABLE
    assert str(caught.value) == "unavailable"


def test_callback_rejects_lone_unicode_surrogate_before_database_access():
    body = json.dumps({"Token": "0" * 64, "Extra": chr(0xD800)}).encode()
    with pytest.raises(PaymentGatewayError) as caught:
        gateway_module().TBankGateway(config()).authenticate_notification(
            IncomingPaymentNotification(headers={}, body=body)
        )
    assert caught.value.category == PaymentGatewayErrorCategory.INVALID_RESPONSE
    assert str(caught.value) == "invalid_response"
    assert caught.value.__suppress_context__ is True
