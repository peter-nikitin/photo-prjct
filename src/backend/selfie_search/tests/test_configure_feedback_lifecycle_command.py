from __future__ import annotations

import hashlib
from copy import deepcopy
from io import StringIO
from typing import Any
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from ingestion.tests.fakes import client_error


class LifecycleClient:
    def __init__(self, original: dict[str, Any] | None = None) -> None:
        self.original = deepcopy(original)
        self.current = deepcopy(original)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.versioning: dict[str, Any] = {}
        self.object_lock: dict[str, Any] = {}
        self.readback_override: dict[str, Any] | None | object = _UNSET
        self._lifecycle_reads = 0
        self._puts = 0
        self.fail_recovery = False

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_bucket_versioning", kwargs))
        return self.versioning

    def get_object_lock_configuration(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_object_lock_configuration", kwargs))
        return self.object_lock

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
        response["ResponseMetadata"] = {"RequestId": "transport-only"}
        return response

    def put_bucket_lifecycle_configuration(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("put_bucket_lifecycle_configuration", kwargs))
        self._puts += 1
        if self.fail_recovery and self._puts == 2:
            raise client_error(500, "recovery-secret")
        self.current = deepcopy(kwargs["LifecycleConfiguration"])
        return {}

    def delete_bucket_lifecycle(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("delete_bucket_lifecycle", kwargs))
        self.current = None
        return {}


_UNSET = object()


@override_settings(
    SELFIE_FEEDBACK_S3_BUCKET="feedback-private",
    SELFIE_FEEDBACK_KMS_KEY_ID="kms-feedback-key",
)
class ConfigureFeedbackLifecycleCommandTests(SimpleTestCase):
    def command_args(self) -> tuple[str, str, str]:
        digest = hashlib.sha256(b"feedback-private\x00kms-feedback-key").hexdigest()
        return ("--confirm-lifecycle-mutation", "--expected-storage-digest", digest)

    def test_requires_confirmation_and_digest_before_any_mutation(self) -> None:
        for args, message in (
            (("--expected-storage-digest", self.command_args()[-1]), "confirm"),
            (("--confirm-lifecycle-mutation", "--expected-storage-digest", "wrong"), "digest"),
        ):
            with self.subTest(message=message):
                client = LifecycleClient()
                with patch(
                    "selfie_search.management.commands.configure_selfie_feedback_lifecycle.boto3.client",
                    return_value=client,
                ):
                    with self.assertRaisesMessage(CommandError, message):
                        call_command("configure_selfie_feedback_lifecycle", *args)
                self.assertEqual(client.calls, [])

    def test_refuses_versioning_or_object_lock_before_lifecycle_mutation(self) -> None:
        for versioning, object_lock, message in (
            ({"Status": "Suspended"}, {}, "versioning"),
            ({}, {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}, "Object Lock"),
        ):
            with self.subTest(message=message):
                client = LifecycleClient()
                client.versioning = versioning
                client.object_lock = object_lock
                with patch(
                    "selfie_search.management.commands.configure_selfie_feedback_lifecycle.boto3.client",
                    return_value=client,
                ):
                    with self.assertRaisesMessage(CommandError, message):
                        call_command("configure_selfie_feedback_lifecycle", *self.command_args())
                self.assertNotIn(
                    "put_bucket_lifecycle_configuration",
                    [operation for operation, _ in client.calls],
                )

    def test_writes_exact_document_and_recovers_original_on_wrong_readback(self) -> None:
        client = LifecycleClient({"Rules": []})
        client.readback_override = {"Rules": []}
        output = StringIO()

        with patch(
            "selfie_search.management.commands.configure_selfie_feedback_lifecycle.boto3.client",
            return_value=client,
        ):
            with self.assertRaisesMessage(CommandError, "readback"):
                call_command(
                    "configure_selfie_feedback_lifecycle", *self.command_args(), stdout=output
                )

        put_calls = [kwargs for operation, kwargs in client.calls if operation.startswith("put_")]
        self.assertEqual(
            put_calls[0]["LifecycleConfiguration"]["Rules"][0]["Expiration"], {"Days": 30}
        )
        self.assertEqual(put_calls[-1]["LifecycleConfiguration"], {"Rules": []})
        self.assertIn("selfie-feedback-lifecycle-mutation-recovery-ok", output.getvalue())
