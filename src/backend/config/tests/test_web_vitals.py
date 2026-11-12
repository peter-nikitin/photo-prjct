import json
from unittest.mock import patch

from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from prometheus_client import REGISTRY, generate_latest


class WebVitalsReceiverTests(TestCase):
    def setUp(self) -> None:
        self.client = Client(enforce_csrf_checks=True)
        self.url = reverse("web_vitals")
        self.headers = {"HTTP_SEC_FETCH_SITE": "same-origin"}

    def post(self, data: object, **extra):
        return self.client.post(
            self.url,
            data=json.dumps(data),
            content_type="application/json",
            **{**self.headers, **extra},
        )

    def test_accepts_anonymous_packet_without_database_or_session_and_exports_all_samples(
        self,
    ) -> None:
        labels = {"page": "gallery", "viewport": "narrow"}
        samples = {
            ("lcp_seconds", "2.5", 2.5),
            ("inp_seconds", "0.2", 0.2),
            ("cls", "0.1", 0.1),
            ("ttfb_seconds", "0.8", 0.8),
        }

        def sample(name: str, extra: dict[str, str] | None = None) -> float:
            return REGISTRY.get_sample_value(name, {**labels, **(extra or {})}) or 0.0

        before = {
            name: (
                sample(f"findme_web_vitals_{name}_count"),
                sample(f"findme_web_vitals_{name}_sum"),
                sample(f"findme_web_vitals_{name}_bucket", {"le": bucket}),
            )
            for name, bucket, _ in samples
        }
        with CaptureQueriesContext(connection) as queries:
            response = self.post(
                {
                    "page": "gallery",
                    "viewport": "narrow",
                    "metrics": {"LCP": 2500, "INP": 200, "CLS": 0.1, "TTFB": 800},
                }
            )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(len(queries), 0)
        self.assertNotIn("Set-Cookie", response.headers)
        metrics = generate_latest(REGISTRY).decode()
        for name, bucket, expected_value in samples:
            previous_count, previous_sum, previous_bucket = before[name]
            self.assertEqual(sample(f"findme_web_vitals_{name}_count"), previous_count + 1)
            self.assertAlmostEqual(
                sample(f"findme_web_vitals_{name}_sum"), previous_sum + expected_value
            )
            self.assertEqual(
                sample(f"findme_web_vitals_{name}_bucket", {"le": bucket}),
                previous_bucket + 1,
            )
        self.assertNotIn('route="web_vitals"', metrics)

    def test_accepts_public_pages_beyond_event_routes(self) -> None:
        for page in ("legal", "photographer_login", "cart", "orders"):
            with self.subTest(page=page):
                self.assertEqual(
                    self.post(
                        {"page": page, "viewport": "wide", "metrics": {"TTFB": 400}}
                    ).status_code,
                    204,
                )

    def test_rejects_whole_invalid_packet_before_any_observation(self) -> None:
        valid = {"page": "catalog", "viewport": "wide", "metrics": {"LCP": 1200}}
        bad = [
            {**valid, "page": "order"},
            {**valid, "viewport": "tablet"},
            {**valid, "url": "/secret"},
            {**valid, "metrics": {}},
            {**valid, "metrics": {"LCP": 1200, "FCP": 10}},
            {**valid, "metrics": {"LCP": 1200, "CLS": -1}},
            {**valid, "metrics": {"LCP": True}},
            {**valid, "metrics": {"LCP": 1e309}},
            {**valid, "metrics": {"LCP": 1e308}},
            {**valid, "metrics": {"LCP": 1200, "TTFB": -1}},
        ]
        with patch("config.web_vitals.observe_web_vitals") as observe:
            for payload in bad:
                with self.subTest(payload=payload):
                    self.assertEqual(self.post(payload).status_code, 400)
            observe.assert_not_called()

    def test_rejects_cross_site_wrong_content_type_and_oversized_body(self) -> None:
        valid = {"page": "catalog", "viewport": "wide", "metrics": {"CLS": 0}}
        with patch("config.web_vitals.observe_web_vitals") as observe:
            self.assertEqual(self.post(valid, HTTP_SEC_FETCH_SITE="cross-site").status_code, 403)
            self.assertEqual(self.post(valid, HTTP_SEC_FETCH_SITE="none").status_code, 403)
            self.assertEqual(
                self.client.post(
                    self.url, data="{}", content_type="text/plain", **self.headers
                ).status_code,
                415,
            )
            self.assertEqual(
                self.client.post(
                    self.url, data=b"x" * 1025, content_type="application/json", **self.headers
                ).status_code,
                413,
            )
            self.assertEqual(
                self.client.post(
                    self.url, data=b"[" * 1024, content_type="application/json", **self.headers
                ).status_code,
                400,
            )
            observe.assert_not_called()

    def test_parser_depth_error_is_a_bad_request(self) -> None:
        with patch("config.web_vitals.json.loads", side_effect=RecursionError):
            response = self.post({"page": "catalog", "viewport": "wide", "metrics": {"LCP": 1000}})
        self.assertEqual(response.status_code, 400)
