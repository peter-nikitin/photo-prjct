from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError
from django.test import TestCase

from feature_flags.models import FeatureFlag
from feature_flags.services import is_enabled


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
