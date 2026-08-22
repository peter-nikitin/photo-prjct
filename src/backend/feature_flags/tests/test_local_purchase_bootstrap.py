from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from feature_flags.models import FeatureFlag
from feature_flags.registry import FEATURE_DEFINITIONS


class LocalPurchaseBootstrapTests(TestCase):
    @override_settings(DEBUG=True)
    def test_bootstrap_enables_only_the_five_purchase_review_flags(self) -> None:
        call_command("sync_feature_flags", verbosity=0)
        call_command("bootstrap_local_purchase_review", verbosity=0)

        self.assertEqual(
            list(FeatureFlag.objects.values_list("key", "description", "state")),
            sorted(
                (definition.key, definition.description, FeatureFlag.State.ON)
                for definition in FEATURE_DEFINITIONS
            ),
        )

    @override_settings(DEBUG=False)
    def test_bootstrap_refuses_non_debug_runtime(self) -> None:
        with self.assertRaisesRegex(CommandError, "DEBUG=True"):
            call_command("bootstrap_local_purchase_review", verbosity=0)

        self.assertFalse(FeatureFlag.objects.exists())
