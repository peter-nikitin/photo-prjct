from __future__ import annotations

import json
from datetime import date
from io import StringIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from picflow.models import Event


class ReportFaceClusterExpansionCommandTests(TestCase):
    def setUp(self) -> None:
        owner = get_user_model().objects.create_user(username="report-command-owner")
        self.event = Event.objects.create(
            name="Command report event",
            slug=f"command-report-{uuid4().hex[:8]}",
            start_date=date(2026, 8, 5),
            end_date=date(2026, 8, 5),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        self.event_sentinel = str(self.event.pk)
        del owner

    def test_command_emits_one_bounded_json_object_and_does_not_echo_event(self) -> None:
        output = StringIO()

        call_command(
            "report_face_cluster_expansion",
            "--start",
            "2026-08-05",
            "--end",
            "2026-08-06",
            "--event",
            str(self.event.pk),
            stdout=output,
        )

        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertIsInstance(payload, dict)
        self.assertNotIn(self.event_sentinel, output.getvalue())
        self.assertLess(len(lines[0].encode()), 16_384)

    def test_command_rejects_invalid_arguments_without_echoing_raw_values(self) -> None:
        for args, expected in (
            (("--start", "not-a-date", "--end", "2026-08-06"), "--start"),
            (("--start", "20260805", "--end", "2026-08-06"), "--start"),
            (("--start", "2026-W32-3", "--end", "2026-08-06"), "--start"),
            (("--start", "2026-08-06", "--end", "2026-08-05"), "bounds"),
            (
                ("--start", "2026-08-05", "--end", "2026-08-06", "--event", "secret-event"),
                "--event",
            ),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(CommandError) as raised:
                    call_command("report_face_cluster_expansion", *args)
                self.assertIn(expected, str(raised.exception))
                self.assertNotIn("secret-event", str(raised.exception))

    def test_command_rejects_empty_or_whitespace_event_instead_of_widening_scope(self) -> None:
        for raw_event in ("", " ", "\t"):
            with self.subTest(raw_event=repr(raw_event)):
                with self.assertRaises(CommandError) as raised:
                    call_command(
                        "report_face_cluster_expansion",
                        "--start",
                        "2026-08-05",
                        "--end",
                        "2026-08-06",
                        "--event",
                        raw_event,
                    )
                self.assertIn("--event", str(raised.exception))
