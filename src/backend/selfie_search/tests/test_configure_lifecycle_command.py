from __future__ import annotations

from copy import deepcopy
from io import StringIO
from typing import Any
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from ingestion.tests.fakes import client_error

EXISTING = {
    "Rules": [
        {
            "ID": "incoming-expire",
            "Status": "Enabled",
            "Filter": {"Prefix": "incoming/"},
            "Expiration": {"Days": 1},
        }
    ]
}


class LifecycleClient:
    def __init__(self, original: dict[str, Any] | None = EXISTING) -> None:
        self.original = deepcopy(original)
        self.current = deepcopy(original)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.versioning: dict[str, Any] = {}
        self.readback_override: dict[str, Any] | None | object = _UNSET
        self.include_response_metadata = False
        self.fail_recovery = False
        self._lifecycle_reads = 0
        self._puts = 0

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_bucket_versioning", kwargs))
        return self.versioning

    def get_bucket_lifecycle_configuration(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_bucket_lifecycle_configuration", kwargs))
        self._lifecycle_reads += 1
        value = (
            self.current
            if self._lifecycle_reads == 1 or self.readback_override is _UNSET
            else self.readback_override
        )
        if value is None:
            raise client_error(404, "NoSuchLifecycleConfiguration")
        assert isinstance(value, dict)
        response = deepcopy(value)
        if self.include_response_metadata:
            response["ResponseMetadata"] = {"RequestId": "not-a-lifecycle-rule"}
        return response

    def put_bucket_lifecycle_configuration(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("put_bucket_lifecycle_configuration", kwargs))
        self._puts += 1
        if self.fail_recovery and self._puts == 2:
            raise client_error(500, "restore-secret")
        self.current = deepcopy(kwargs["LifecycleConfiguration"])
        return {}

    def delete_bucket_lifecycle(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("delete_bucket_lifecycle", kwargs))
        self.current = None
        return {}


_UNSET = object()


@override_settings(PRIVATE_MEDIA_S3_BUCKET="private-selfies")
class ConfigureSelfieSearchLifecycleCommandTests(SimpleTestCase):
    def command_args(self) -> tuple[str, str, str]:
        return (
            "--confirm-lifecycle-mutation",
            "--expected-bucket-digest",
            "edb9503176db8bc1d2bcb7f6925ff0bbca56fb2472670769c6c03d3d260d3aab",
        )

    def test_confirmation_digest_versioning_and_collision_fail_before_put(self) -> None:
        cases = (
            (
                ("--expected-bucket-digest", self.command_args()[-1]),
                LifecycleClient(),
                "--confirm-lifecycle-mutation",
            ),
            (
                ("--confirm-lifecycle-mutation", "--expected-bucket-digest", "wrong"),
                LifecycleClient(),
                "digest",
            ),
            (self.command_args(), LifecycleClient(), "versioning"),
            (
                self.command_args(),
                LifecycleClient(
                    {"Rules": [{"ID": "other", "Filter": {"Prefix": "selfie-search/"}}]}
                ),
                "collision",
            ),
        )
        cases[2][1].versioning = {"Status": "Enabled"}

        for args, client, message in cases:
            with self.subTest(message=message):
                with patch(
                    "selfie_search.management.commands.configure_selfie_search_lifecycle.boto3.client",
                    return_value=client,
                ):
                    with self.assertRaisesMessage(CommandError, message):
                        call_command("configure_selfie_search_lifecycle", *args)
                self.assertNotIn(
                    "put_bucket_lifecycle_configuration",
                    [operation for operation, _ in client.calls],
                )

    def test_preserves_existing_rules_and_verifies_full_readback(self) -> None:
        client = LifecycleClient()
        client.include_response_metadata = True
        output = StringIO()

        with patch(
            "selfie_search.management.commands.configure_selfie_search_lifecycle.boto3.client",
            return_value=client,
        ):
            call_command("configure_selfie_search_lifecycle", *self.command_args(), stdout=output)

        written = client.calls[2][1]["LifecycleConfiguration"]
        self.assertEqual(written["Rules"][:1], EXISTING["Rules"])
        self.assertEqual(written, client.current)
        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "selfie-search-lifecycle-mutation-preflight-ok",
                "selfie-search-lifecycle-mutation-put-ok",
                "selfie-search-lifecycle-mutation-readback-ok",
            ],
        )

    def test_no_existing_lifecycle_creates_one_exact_rule(self) -> None:
        client = LifecycleClient(None)

        with patch(
            "selfie_search.management.commands.configure_selfie_search_lifecycle.boto3.client",
            return_value=client,
        ):
            call_command("configure_selfie_search_lifecycle", *self.command_args())

        self.assertIsNotNone(client.current)
        assert client.current is not None
        self.assertEqual(len(client.current["Rules"]), 1)

    def test_verification_failure_restores_existing_document(self) -> None:
        client = LifecycleClient()
        client.readback_override = {"Rules": []}

        with patch(
            "selfie_search.management.commands.configure_selfie_search_lifecycle.boto3.client",
            return_value=client,
        ):
            with self.assertRaisesMessage(CommandError, "readback"):
                call_command("configure_selfie_search_lifecycle", *self.command_args())

        put_calls = [kwargs for operation, kwargs in client.calls if operation.startswith("put_")]
        self.assertEqual(put_calls[-1]["LifecycleConfiguration"], EXISTING)

    def test_verification_failure_deletes_lifecycle_when_original_was_absent(self) -> None:
        client = LifecycleClient(None)
        client.readback_override = None

        with patch(
            "selfie_search.management.commands.configure_selfie_search_lifecycle.boto3.client",
            return_value=client,
        ):
            with self.assertRaisesMessage(CommandError, "readback"):
                call_command("configure_selfie_search_lifecycle", *self.command_args())

        self.assertIn("delete_bucket_lifecycle", [operation for operation, _ in client.calls])

    def test_recovery_failure_is_sanitized(self) -> None:
        client = LifecycleClient()
        client.readback_override = {"Rules": []}
        client.fail_recovery = True

        with patch(
            "selfie_search.management.commands.configure_selfie_search_lifecycle.boto3.client",
            return_value=client,
        ):
            with self.assertRaisesMessage(CommandError, "recovery failed") as caught:
                call_command("configure_selfie_search_lifecycle", *self.command_args())

        self.assertNotIn("secret", str(caught.exception))
