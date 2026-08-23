from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from feature_flags.models import FeatureFlag
from feature_flags.registry import (
    PAID_EVENTS,
    PAID_PHOTO_CART,
    PAID_PHOTO_PAYMENT_SIMULATOR,
    PAID_PHOTO_PURCHASE,
    PAID_WATERMARKED_PREVIEWS,
)

_LOCAL_PURCHASE_REVIEW_DEFINITIONS = (
    PAID_EVENTS,
    PAID_WATERMARKED_PREVIEWS,
    PAID_PHOTO_CART,
    PAID_PHOTO_PURCHASE,
    PAID_PHOTO_PAYMENT_SIMULATOR,
)


class Command(BaseCommand):
    help = "Enable the complete paid-photo flow in an explicit local debug runtime."

    def handle(self, *args, **options) -> None:
        del args, options
        if settings.DEBUG is not True:
            raise CommandError("Local purchase bootstrap requires DEBUG=True.")
        FeatureFlag.objects.filter(
            key__in=[definition.key for definition in _LOCAL_PURCHASE_REVIEW_DEFINITIONS]
        ).update(state=FeatureFlag.State.ON)
        self.stdout.write(self.style.SUCCESS("Local paid-photo flags are on."))
