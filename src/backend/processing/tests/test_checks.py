from django.core.checks import run_checks
from django.test import SimpleTestCase, override_settings


class CaptureMetadataConfigurationChecksTests(SimpleTestCase):
    @override_settings(PHOTO_PROCESSING_MAX_REQUEST_BYTES=8_192)
    def test_request_limit_matching_immutable_terminal_bound_passes(self) -> None:
        errors = run_checks()

        self.assertNotIn("processing.E001", [error.id for error in errors])

    @override_settings(PHOTO_PROCESSING_MAX_REQUEST_BYTES=8_191)
    def test_request_limit_below_immutable_terminal_bound_fails(self) -> None:
        errors = run_checks()

        self.assertIn("processing.E001", [error.id for error in errors])
