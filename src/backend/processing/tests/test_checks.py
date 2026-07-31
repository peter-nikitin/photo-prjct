from django.core.checks import run_checks
from django.test import SimpleTestCase, override_settings

from processing.services.enrollment import FACE_EMBEDDING_CONFIGURATION


class CaptureMetadataConfigurationChecksTests(SimpleTestCase):
    @override_settings(PHOTO_PROCESSING_MAX_REQUEST_BYTES=128 * 1024)
    def test_request_limit_matching_immutable_terminal_bound_passes(self) -> None:
        errors = run_checks()

        self.assertNotIn("processing.E001", [error.id for error in errors])

    @override_settings(PHOTO_PROCESSING_MAX_REQUEST_BYTES=8_191)
    def test_request_limit_below_immutable_terminal_bound_fails(self) -> None:
        errors = run_checks()

        self.assertIn("processing.E001", [error.id for error in errors])

    @override_settings(PHOTO_PROCESSING_MAX_REQUEST_BYTES=(128 * 1024) - 1)
    def test_request_limit_below_v2_face_terminal_bound_fails(self) -> None:
        errors = run_checks()

        self.assertIn("processing.E001", [error.id for error in errors])

    def test_v2_face_terminal_bound_is_128_kib(self) -> None:
        worker = FACE_EMBEDDING_CONFIGURATION["worker"]

        self.assertIsInstance(worker, dict)
        assert isinstance(worker, dict)
        self.assertEqual(worker["terminal_result_max_bytes"], 128 * 1024)
        self.assertEqual(worker["api_response_max_bytes"], 128 * 1024)
