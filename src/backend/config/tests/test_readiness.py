from unittest.mock import patch

from django.db import OperationalError
from django.test import SimpleTestCase


class ReadinessTests(SimpleTestCase):
    @patch("config.views.connection")
    def test_database_available(self, connection) -> None:
        response = self.client.get("/ready/")
        self.assertEqual(response.status_code, 200)
        connection.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
            "SELECT 1"
        )

    @patch("config.views.connection")
    def test_database_unavailable(self, connection) -> None:
        connection.cursor.side_effect = OperationalError("database unavailable")
        response = self.client.get("/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"database unavailable", response.content)
