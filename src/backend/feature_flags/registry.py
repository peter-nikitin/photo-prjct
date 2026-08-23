import re
from dataclasses import dataclass
from typing import Final

_KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_MAX_KEY_LENGTH: Final = 100
_MAX_DESCRIPTION_LENGTH: Final = 255


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    key: str
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not _KEY_PATTERN.fullmatch(self.key):
            raise ValueError("Feature-definition keys must be lowercase and hyphenated.")
        if len(self.key) > _MAX_KEY_LENGTH:
            raise ValueError("Feature-definition keys exceed the persisted limit.")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("Feature-definition descriptions must not be empty.")
        if len(self.description) > _MAX_DESCRIPTION_LENGTH:
            raise ValueError("Feature-definition descriptions exceed the persisted limit.")


def validate_feature_definitions(definitions: tuple[FeatureDefinition, ...]) -> None:
    keys = {definition.key for definition in definitions}
    if len(keys) != len(definitions):
        raise ValueError("Feature-definition keys must be unique.")


PAID_EVENTS: Final = FeatureDefinition("paid-events", "Show paid events")
PAID_WATERMARKED_PREVIEWS: Final = FeatureDefinition(
    "paid-watermarked-previews", "Show accepted paid watermarked previews"
)
PAID_PHOTO_CART: Final = FeatureDefinition("paid-photo-cart", "Allow paid photo cart selection")
PAID_PHOTO_PURCHASE: Final = FeatureDefinition(
    "paid-photo-purchase", "Allow paid photo checkout and fulfillment"
)
PAID_PHOTO_PAYMENT_SIMULATOR: Final = FeatureDefinition(
    "paid-photo-payment-simulator", "Use the feature-gated test payment screen"
)

FEATURE_DEFINITIONS: Final = (
    PAID_EVENTS,
    PAID_WATERMARKED_PREVIEWS,
    PAID_PHOTO_CART,
    PAID_PHOTO_PURCHASE,
    PAID_PHOTO_PAYMENT_SIMULATOR,
)

validate_feature_definitions(FEATURE_DEFINITIONS)
