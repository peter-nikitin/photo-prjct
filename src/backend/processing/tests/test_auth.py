import json

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from processing.auth import require_worker_token


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
