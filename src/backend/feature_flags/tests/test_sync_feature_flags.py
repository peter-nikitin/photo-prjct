from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from feature_flags.models import FeatureFlag
from feature_flags.registry import FEATURE_DEFINITIONS, FeatureDefinition


class SyncFeatureFlagsTests(TestCase):
    def sync(self) -> str:
        output = StringIO()
        call_command("sync_feature_flags", stdout=output, verbosity=0)
        return output.getvalue()

    def test_creates_every_registered_definition_off_with_sanitized_counts(self) -> None:
        output = self.sync()

        self.assertEqual(
            list(FeatureFlag.objects.values_list("key", "description", "state")),
            sorted(
                (definition.key, definition.description, FeatureFlag.State.OFF)
                for definition in FEATURE_DEFINITIONS
            ),
        )
        self.assertEqual(
            output,
            "Feature flags synchronized: created=5 updated=0 preserved=0 deleted=0.\n",
        )

    def test_preserves_registered_states_and_creation_timestamp(self) -> None:
        states = (FeatureFlag.State.OFF, FeatureFlag.State.STAFF, FeatureFlag.State.ON)
        flags = [
            FeatureFlag.objects.create(
                key=definition.key,
                description=definition.description,
                state=state,
            )
            for definition, state in zip(FEATURE_DEFINITIONS[:3], states, strict=True)
        ]
        created_at_by_key = {flag.key: flag.created_at for flag in flags}

        output = self.sync()

        self.assertEqual(
            list(
                FeatureFlag.objects.filter(key__in=[flag.key for flag in flags])
                .order_by("key")
                .values_list("key", "state", "created_at")
            ),
            sorted(
                (flag.key, state, created_at_by_key[flag.key])
                for flag, state in zip(flags, states, strict=True)
            ),
        )
        self.assertEqual(
            output,
            "Feature flags synchronized: created=2 updated=0 preserved=3 deleted=0.\n",
        )

    def test_refreshes_description_without_changing_state_or_creation_timestamp(self) -> None:
        definition = FEATURE_DEFINITIONS[0]
        flag = FeatureFlag.objects.create(
            key=definition.key,
            description="Old operator wording",
            state=FeatureFlag.State.ON,
        )
        created_at = flag.created_at

        output = self.sync()

        flag.refresh_from_db()
        self.assertEqual(flag.description, definition.description)
        self.assertEqual(flag.state, FeatureFlag.State.ON)
        self.assertEqual(flag.created_at, created_at)
        self.assertEqual(
            output,
            "Feature flags synchronized: created=4 updated=1 preserved=0 deleted=0.\n",
        )

    def test_deletes_stale_rows_and_is_idempotent(self) -> None:
        FeatureFlag.objects.create(
            key="retired-release",
            description="Remove this stale definition",
            state=FeatureFlag.State.ON,
        )

        self.assertEqual(
            self.sync(),
            "Feature flags synchronized: created=5 updated=0 preserved=0 deleted=1.\n",
        )
        self.assertFalse(FeatureFlag.objects.filter(key="retired-release").exists())
        self.assertEqual(
            self.sync(),
            "Feature flags synchronized: created=0 updated=0 preserved=5 deleted=0.\n",
        )

    def test_invalid_registry_aborts_before_mutating_existing_rows(self) -> None:
        existing = FeatureFlag.objects.create(
            key="retired-release",
            description="Do not delete before validation",
            state=FeatureFlag.State.ON,
        )
        invalid_definitions = (
            FeatureDefinition("duplicate-release", "First duplicate definition"),
            FeatureDefinition("duplicate-release", "Second duplicate definition"),
        )

        with (
            patch(
                "feature_flags.management.commands.sync_feature_flags.FEATURE_DEFINITIONS",
                invalid_definitions,
            ),
            self.assertRaisesRegex(ValueError, "unique"),
        ):
            self.sync()

        existing.refresh_from_db()
        self.assertEqual(existing.state, FeatureFlag.State.ON)
        self.assertEqual(existing.description, "Do not delete before validation")
        self.assertEqual(FeatureFlag.objects.count(), 1)
