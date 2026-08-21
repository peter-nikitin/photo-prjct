from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError
from django.test import TestCase

from feature_flags.models import FeatureFlag
from feature_flags.services import is_enabled
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
        for user in (self.anonymous, self.inactive_staff, self.ordinary_user, self.active_staff):
            with self.subTest(user=user):
                self.assertFalse(is_enabled("missing-release", user))

    def test_off_flag_blocks_every_caller(self) -> None:
        FeatureFlag.objects.create(
            key="off-release", description="Keep the release hidden", state=FeatureFlag.State.OFF
        )

        for user in (self.anonymous, self.inactive_staff, self.ordinary_user, self.active_staff):
            with self.subTest(user=user):
                self.assertFalse(is_enabled("off-release", user))

    def test_staff_flag_allows_only_active_staff(self) -> None:
        FeatureFlag.objects.create(
            key="staff-release",
            description="Preview the release for staff",
            state=FeatureFlag.State.STAFF,
        )

        self.assertFalse(is_enabled("staff-release", self.anonymous))
        self.assertFalse(is_enabled("staff-release", self.inactive_staff))
        self.assertFalse(is_enabled("staff-release", self.ordinary_user))
        self.assertTrue(is_enabled("staff-release", self.active_staff))

    def test_on_flag_allows_every_caller(self) -> None:
        FeatureFlag.objects.create(
            key="on-release", description="Release is public", state=FeatureFlag.State.ON
        )

        for user in (self.anonymous, self.inactive_staff, self.ordinary_user, self.active_staff):
            with self.subTest(user=user):
                self.assertTrue(is_enabled("on-release", user))

    def test_next_evaluation_uses_current_database_state(self) -> None:
        flag = FeatureFlag.objects.create(
            key="changing-release",
            description="Change release exposure immediately",
            state=FeatureFlag.State.OFF,
        )

        self.assertFalse(is_enabled(flag.key, self.active_staff))
        flag.state = FeatureFlag.State.STAFF
        flag.save(update_fields=("state",))
        self.assertTrue(is_enabled(flag.key, self.active_staff))
        self.assertFalse(is_enabled(flag.key, self.ordinary_user))
        flag.state = FeatureFlag.State.ON
        flag.save(update_fields=("state",))
        self.assertTrue(is_enabled(flag.key, self.ordinary_user))

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
        states = {"test-staff-release": FEATURE_FLAG_STAFF}
        ordinary = get_user_model().objects.create_user(username="override-ordinary")
        inactive_staff = get_user_model().objects.create_user(
            username="override-inactive-staff", is_staff=True, is_active=False
        )
        staff = get_user_model().objects.create_user(username="override-staff", is_staff=True)

        with override_feature_flags(states):
            self.assertFalse(is_enabled("test-staff-release", AnonymousUser()))
            self.assertFalse(is_enabled("test-staff-release", ordinary))
            self.assertFalse(is_enabled("test-staff-release", inactive_staff))
            self.assertTrue(is_enabled("test-staff-release", staff))

        self.assertFalse(FeatureFlag.objects.filter(key="test-staff-release").exists())

    def test_override_reads_mutable_states_and_treats_unknown_keys_as_off(self) -> None:
        states = {"test-transition-release": FEATURE_FLAG_OFF}
        staff = get_user_model().objects.create_user(username="transition-staff", is_staff=True)

        with override_feature_flags(states):
            self.assertFalse(is_enabled("test-transition-release", staff))
            self.assertFalse(is_enabled("unknown-test-release", staff))
            states["test-transition-release"] = FEATURE_FLAG_STAFF
            self.assertTrue(is_enabled("test-transition-release", staff))
            states["test-transition-release"] = FEATURE_FLAG_ON
            self.assertTrue(is_enabled("test-transition-release", AnonymousUser()))

    def test_nested_overrides_restore_the_outer_mapping(self) -> None:
        outer_states = {"nested-release": FEATURE_FLAG_STAFF}
        inner_states = {"nested-release": FEATURE_FLAG_ON}
        staff = get_user_model().objects.create_user(
            username="nested-override-staff", is_staff=True
        )

        with override_feature_flags(outer_states):
            self.assertFalse(is_enabled("nested-release", AnonymousUser()))
            self.assertTrue(is_enabled("nested-release", staff))
            with override_feature_flags(inner_states):
                self.assertTrue(is_enabled("nested-release", AnonymousUser()))
            self.assertFalse(is_enabled("nested-release", AnonymousUser()))
            self.assertTrue(is_enabled("nested-release", staff))

        self.assertFalse(is_enabled("nested-release", AnonymousUser()))

    def test_override_is_visible_to_child_threads_without_leaking_after_exit(self) -> None:
        states = {"thread-release": FEATURE_FLAG_STAFF}
        staff = get_user_model().objects.create_user(
            username="thread-override-staff", is_staff=True
        )

        with patch(
            "feature_flags.services._database_state_for", return_value=FEATURE_FLAG_OFF
        ) as database_state:
            with override_feature_flags(states), ThreadPoolExecutor(max_workers=1) as executor:
                self.assertTrue(executor.submit(is_enabled, "thread-release", staff).result())
                states["thread-release"] = FEATURE_FLAG_ON
                self.assertTrue(
                    executor.submit(is_enabled, "thread-release", AnonymousUser()).result()
                )
                self.assertFalse(
                    executor.submit(is_enabled, "unknown-thread-release", staff).result()
                )

            self.assertFalse(is_enabled("thread-release", AnonymousUser()))

        database_state.assert_called_once_with("thread-release")
