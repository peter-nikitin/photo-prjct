from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from feature_flags.models import FeatureFlag


class LocalPurchaseBootstrapTests(TestCase):
    @override_settings(DEBUG=True)
    def test_bootstrap_enables_only_the_five_purchase_review_flags(self) -> None:
        call_command("bootstrap_local_purchase_review", verbosity=0)

        self.assertEqual(
            dict(FeatureFlag.objects.values_list("key", "state")),
            {
                "paid-events": FeatureFlag.State.ON,
                "paid-watermarked-previews": FeatureFlag.State.ON,
                "paid-photo-cart": FeatureFlag.State.ON,
                "paid-photo-purchase": FeatureFlag.State.ON,
                "paid-photo-payment-simulator": FeatureFlag.State.ON,
            },
        )

    @override_settings(DEBUG=False)
    def test_bootstrap_refuses_non_debug_runtime(self) -> None:
        with self.assertRaisesRegex(CommandError, "DEBUG=True"):
            call_command("bootstrap_local_purchase_review", verbosity=0)

        self.assertFalse(FeatureFlag.objects.exists())
