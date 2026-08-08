from datetime import UTC, datetime

from django.test import SimpleTestCase

from processing.results import parse_canonical_timestamp


class CanonicalTimestampTests(SimpleTestCase):
    def test_accepts_the_worker_timestamp_contract(self) -> None:
        self.assertEqual(
            parse_canonical_timestamp("2026-08-08T12:34:56.123456Z"),
            datetime(2026, 8, 8, 12, 34, 56, 123456, tzinfo=UTC),
        )

    def test_rejects_parseable_noncanonical_forms(self) -> None:
        for value in ("2026-08-08 12:34:56Z", "2026-08-08T12:34:56.1234567Z"):
            with self.subTest(value=value):
                self.assertIsNone(parse_canonical_timestamp(value))
