from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError
from django.test import TestCase

from feature_flags.models import FeatureFlag
from feature_flags.registry import FeatureDefinition
from feature_flags.services import is_enabled, is_server_enabled
from feature_flags.states import FEATURE_FLAG_OFF, FEATURE_FLAG_ON, FEATURE_FLAG_STAFF
from feature_flags.testing import override_feature_flags


class FeatureFlagServiceTests(TestCase):
    def setUp(self) -> None:
        users = get_user_model().objects
        self.anonymous = AnonymousUser()
        self.inactive_staff = users.create_user(
            username="inactive-staff", is_active=False, is_staff=True
        )
        self.ordinary_user = users.create_user(username="ordinary-user")
        self.active_staff = users.create_user(username="active-staff", is_staff=True)

    def test_missing_flag_is_disabled_for_every_caller(self) -> None:
        definition = FeatureDefinition("missing-release", "A missing release")
        for user in (self.anonymous, self.inactive_staff, self.ordinary_user, self.active_staff):
            with self.subTest(user=user):
                self.assertFalse(is_enabled(definition, user))

    def test_off_flag_blocks_every_caller(self) -> None:
        definition = FeatureDefinition("off-release", "Keep the release hidden")
        FeatureFlag.objects.create(
            key=definition.key, description=definition.description, state=FeatureFlag.State.OFF
        )

        for user in (self.anonymous, self.inactive_staff, self.ordinary_user, self.active_staff):
            with self.subTest(user=user):
                self.assertFalse(is_enabled(definition, user))

    def test_staff_flag_allows_only_active_staff(self) -> None:
        definition = FeatureDefinition("staff-release", "Preview the release for staff")
        FeatureFlag.objects.create(
            key=definition.key,
            description=definition.description,
            state=FeatureFlag.State.STAFF,
        )

        self.assertFalse(is_enabled(definition, self.anonymous))
        self.assertFalse(is_enabled(definition, self.inactive_staff))
        self.assertFalse(is_enabled(definition, self.ordinary_user))
        self.assertTrue(is_enabled(definition, self.active_staff))
        self.assertTrue(is_server_enabled(definition))

    def test_on_flag_allows_every_caller(self) -> None:
        definition = FeatureDefinition("on-release", "Release is public")
        FeatureFlag.objects.create(
            key=definition.key, description=definition.description, state=FeatureFlag.State.ON
        )

        for user in (self.anonymous, self.inactive_staff, self.ordinary_user, self.active_staff):
            with self.subTest(user=user):
                self.assertTrue(is_enabled(definition, user))

    def test_next_evaluation_uses_current_database_state(self) -> None:
        definition = FeatureDefinition("changing-release", "Change release exposure immediately")
        flag = FeatureFlag.objects.create(
            key=definition.key,
            description=definition.description,
            state=FeatureFlag.State.OFF,
        )

        self.assertFalse(is_enabled(definition, self.active_staff))
        flag.state = FeatureFlag.State.STAFF
        flag.save(update_fields=("state",))
        self.assertTrue(is_enabled(definition, self.active_staff))
        self.assertFalse(is_enabled(definition, self.ordinary_user))
        flag.state = FeatureFlag.State.ON
        flag.save(update_fields=("state",))
        self.assertTrue(is_enabled(definition, self.ordinary_user))

    def test_services_reject_raw_string_keys(self) -> None:
        with self.assertRaises(TypeError):
            is_enabled("missing-release", self.active_staff)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            is_server_enabled("missing-release")  # type: ignore[arg-type]

    def test_key_is_unique_and_timestamps_are_recorded(self) -> None:
        flag = FeatureFlag.objects.create(
            key="stable-release", description="A stable code-owned key", state=FeatureFlag.State.OFF
        )

        self.assertIsNotNone(flag.created_at)
        self.assertIsNotNone(flag.updated_at)
        with self.assertRaises(IntegrityError):
            FeatureFlag.objects.create(
                key="stable-release",
                description="A duplicate key must be rejected",
                state=FeatureFlag.State.ON,
            )


class FeatureFlagTestOverrideTests(TestCase):
    def test_override_permits_only_active_staff_in_staff_state_without_a_database_row(self) -> None:
        definition = FeatureDefinition("test-staff-release", "A test-only staff release")
        states = {definition: FEATURE_FLAG_STAFF}
        ordinary = get_user_model().objects.create_user(username="override-ordinary")
        inactive_staff = get_user_model().objects.create_user(
            username="override-inactive-staff", is_staff=True, is_active=False
        )
        staff = get_user_model().objects.create_user(username="override-staff", is_staff=True)

        with override_feature_flags(states):
            self.assertFalse(is_enabled(definition, AnonymousUser()))
            self.assertFalse(is_enabled(definition, ordinary))
            self.assertFalse(is_enabled(definition, inactive_staff))
            self.assertTrue(is_enabled(definition, staff))

        self.assertFalse(FeatureFlag.objects.filter(key=definition.key).exists())

    def test_override_reads_mutable_states_and_treats_unknown_keys_as_off(self) -> None:
        definition = FeatureDefinition("test-transition-release", "A mutable test release")
        unknown = FeatureDefinition("unknown-test-release", "An unknown test release")
        states = {definition: FEATURE_FLAG_OFF}
        staff = get_user_model().objects.create_user(username="transition-staff", is_staff=True)

        with override_feature_flags(states):
            self.assertFalse(is_enabled(definition, staff))
            self.assertFalse(is_enabled(unknown, staff))
            states[definition] = FEATURE_FLAG_STAFF
            self.assertTrue(is_enabled(definition, staff))
            states[definition] = FEATURE_FLAG_ON
            self.assertTrue(is_enabled(definition, AnonymousUser()))

    def test_nested_overrides_restore_the_outer_mapping(self) -> None:
        definition = FeatureDefinition("nested-release", "A nested test release")
        outer_states = {definition: FEATURE_FLAG_STAFF}
        inner_states = {definition: FEATURE_FLAG_ON}
        staff = get_user_model().objects.create_user(
            username="nested-override-staff", is_staff=True
        )

        with override_feature_flags(outer_states):
            self.assertFalse(is_enabled(definition, AnonymousUser()))
            self.assertTrue(is_enabled(definition, staff))
            with override_feature_flags(inner_states):
                self.assertTrue(is_enabled(definition, AnonymousUser()))
            self.assertFalse(is_enabled(definition, AnonymousUser()))
            self.assertTrue(is_enabled(definition, staff))

        self.assertFalse(is_enabled(definition, AnonymousUser()))

    def test_override_is_visible_to_child_threads_without_leaking_after_exit(self) -> None:
        definition = FeatureDefinition("thread-release", "A thread test release")
        unknown = FeatureDefinition("unknown-thread-release", "An unknown thread test release")
        states = {definition: FEATURE_FLAG_STAFF}
        staff = get_user_model().objects.create_user(
            username="thread-override-staff", is_staff=True
        )

        with patch(
            "feature_flags.services._database_state_for", return_value=FEATURE_FLAG_OFF
        ) as database_state:
            with override_feature_flags(states), ThreadPoolExecutor(max_workers=1) as executor:
                self.assertTrue(executor.submit(is_enabled, definition, staff).result())
                states[definition] = FEATURE_FLAG_ON
                self.assertTrue(executor.submit(is_enabled, definition, AnonymousUser()).result())
                self.assertFalse(executor.submit(is_enabled, unknown, staff).result())

            self.assertFalse(is_enabled(definition, AnonymousUser()))

        database_state.assert_called_once_with(definition)
