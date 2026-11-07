from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse


class HealthTests(TestCase):
    def test_health_checks_database(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(self.client.get("/ready/").status_code, 404)

    def test_database_error_returns_unavailable(self):
        with patch("config.views.connection.cursor", side_effect=DatabaseError):
            self.assertEqual(self.client.get(reverse("health")).status_code, 503)
