from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from feature_flags.models import FeatureFlag

_FLAGS = {
    "paid-events": "Show paid events",
    "paid-watermarked-previews": "Show accepted paid watermarked previews",
    "paid-photo-cart": "Allow paid photo cart selection",
    "paid-photo-purchase": "Allow paid photo checkout and fulfillment",
    "paid-photo-payment-simulator": "Use the feature-gated test payment screen",
}


class Command(BaseCommand):
    help = "Enable the complete paid-photo flow in an explicit local debug runtime."

    def handle(self, *args, **options) -> None:
        del args, options
        if settings.DEBUG is not True:
            raise CommandError("Local purchase bootstrap requires DEBUG=True.")
        for key, description in _FLAGS.items():
            FeatureFlag.objects.update_or_create(
                key=key,
                defaults={
                    "description": description,
                    "state": FeatureFlag.State.ON,
                },
            )
        self.stdout.write(self.style.SUCCESS("Local paid-photo flags are on."))
