import json

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from processing.auth import has_worker_token, require_worker_token


@override_settings(
    PHOTO_PROCESSING_ENABLED=True,
    PHOTO_PROCESSING_WORKER_TOKEN="local-secret",
    PHOTO_PROCESSING_FLEET_TOKEN="fleet-secret",
)
class FleetTokenAuthenticationTests(SimpleTestCase):
    @override_settings(
        DEBUG=False,
        PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True,
        PHOTO_PROCESSING_WORKER_TOKEN="fleet-secret",
    )
    def test_equal_stale_local_setting_does_not_disable_production_private_fleet(self):
        request = RequestFactory().post(
            "/",
            HTTP_AUTHORIZATION="Bearer fleet-secret",
            HTTP_X_FINDME_WORKER_TRANSPORT="private-tls",
        )
        self.assertTrue(has_worker_token(request))
        request = RequestFactory().post("/", HTTP_AUTHORIZATION="Bearer fleet-secret")
        self.assertFalse(has_worker_token(request))

    @override_settings(DEBUG=False, PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True)
    def test_production_accepts_only_private_fleet_even_with_stale_local_token(self):
        for marker, token, expected in (
            ("", "local-secret", False),
            ("", "fleet-secret", False),
            ("private-tls", "local-secret", False),
            ("private-tls", "fleet-secret", True),
        ):
            with self.subTest(marker=marker, token=token):
                request = RequestFactory().post(
                    "/",
                    HTTP_AUTHORIZATION=f"Bearer {token}",
                    HTTP_X_FINDME_WORKER_TRANSPORT=marker,
                )
                self.assertEqual(has_worker_token(request), expected)

    @override_settings(DEBUG=True, PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True)
    def test_coordinator_disables_local_development_authorization(self):
        request = RequestFactory().post("/", HTTP_AUTHORIZATION="Bearer local-secret")
        self.assertFalse(has_worker_token(request))

    @override_settings(DEBUG=True, PHOTO_WORKER_POOL_COORDINATOR_ENABLED=False)
    def test_transport_credentials_are_disjoint(self):
        factory = RequestFactory()
        for marker, token, expected in (
            ("private-tls", "fleet-secret", True),
            ("private-tls", "local-secret", False),
            ("", "local-secret", True),
            ("", "fleet-secret", False),
            ("unexpected", "fleet-secret", False),
        ):
            with self.subTest(marker=marker, token=token):
                request = factory.post(
                    "/",
                    HTTP_AUTHORIZATION=f"Bearer {token}",
                    HTTP_X_FINDME_WORKER_TRANSPORT=marker,
                )
                self.assertEqual(has_worker_token(request), expected)

    @override_settings(DEBUG=True, PHOTO_WORKER_POOL_COORDINATOR_ENABLED=False)
    def test_private_transport_fails_closed_on_missing_or_equal_development_token(self):
        request = RequestFactory().post(
            "/",
            HTTP_AUTHORIZATION="Bearer local-secret",
            HTTP_X_FINDME_WORKER_TRANSPORT="private-tls",
        )
        for token in ("", "local-secret"):
            with self.subTest(token=token), override_settings(PHOTO_PROCESSING_FLEET_TOKEN=token):
                self.assertFalse(has_worker_token(request))

    def test_fleet_credential_cannot_be_persisted_in_durable_callback_strings(self):
        from processing.views import _safe_durable_string, _safe_error_detail

        self.assertFalse(_safe_durable_string("build-fleet-secret"))
        self.assertFalse(_safe_error_detail("error-fleet-secret"))
        self.assertTrue(_safe_durable_string("a" * 40))


@override_settings(DEBUG=True, PHOTO_WORKER_POOL_COORDINATOR_ENABLED=False)
class WorkerTokenAuthenticationTests(SimpleTestCase):
    """The production break caught here is accepting a missing or non-bearer credential."""

    def setUp(self) -> None:
        self.factory = RequestFactory()

    @override_settings(PHOTO_PROCESSING_ENABLED=True, PHOTO_PROCESSING_WORKER_TOKEN="worker-secret")
    def test_missing_malformed_and_incorrect_credentials_have_one_sanitized_denial(self) -> None:
        @require_worker_token
        def protected(_request):
            return HttpResponse(status=204)

        responses = [
            protected(self.factory.post("/", HTTP_AUTHORIZATION=value))
            for value in ("", "Basic worker-secret", "Bearer wrong", "Bearer worker-secret extra")
        ]

        self.assertTrue(all(response.status_code == 401 for response in responses))
        self.assertEqual(
            [json.loads(response.content) for response in responses],
            [{"error": {"code": "worker_unauthorized", "message": "Unauthorized."}}] * 4,
        )

    @override_settings(PHOTO_PROCESSING_ENABLED=True, PHOTO_PROCESSING_WORKER_TOKEN="worker-secret")
    def test_exact_bearer_credential_is_authorized(self) -> None:
        @require_worker_token
        def protected(_request):
            return HttpResponse(status=204)

        response = protected(self.factory.post("/", HTTP_AUTHORIZATION="Bearer worker-secret"))

        self.assertEqual(response.status_code, 204)

    @override_settings(
        PHOTO_PROCESSING_ENABLED=False, PHOTO_PROCESSING_WORKER_TOKEN="worker-secret"
    )
    def test_disabled_feature_fails_closed_with_the_same_sanitized_denial(self) -> None:
        @require_worker_token
        def protected(_request):
            return HttpResponse(status=204)

        response = protected(self.factory.post("/", HTTP_AUTHORIZATION="Bearer worker-secret"))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.content)["error"]["code"], "worker_unauthorized")
