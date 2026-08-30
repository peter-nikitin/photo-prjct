from dataclasses import FrozenInstanceError

from django.test import SimpleTestCase

from feature_flags.registry import (
    BULK_PHOTO_DOWNLOAD,
    FEATURE_DEFINITIONS,
    PAID_EVENTS,
    PAID_PHOTO_CART,
    PAID_PHOTO_PAYMENT_SIMULATOR,
    PAID_PHOTO_PURCHASE,
    PAID_WATERMARKED_PREVIEWS,
    FeatureDefinition,
    validate_feature_definitions,
)


class FeatureDefinitionTests(SimpleTestCase):
    def test_definition_is_immutable(self) -> None:
        definition = FeatureDefinition("test-release", "Test the release")

        with self.assertRaises(FrozenInstanceError):
            definition.key = "renamed-release"  # type: ignore[misc]

    def test_definition_rejects_invalid_persisted_values(self) -> None:
        for key, description in (
            ("", "A description"),
            ("Not-lowercase", "A description"),
            ("two--hyphens", "A description"),
            ("a" * 101, "A description"),
            ("valid-release", ""),
            ("valid-release", "a" * 256),
        ):
            with self.subTest(key=key, description=description):
                with self.assertRaises(ValueError):
                    FeatureDefinition(key, description)

    def test_registry_validation_rejects_duplicate_keys(self) -> None:
        first = FeatureDefinition("shared-release", "First definition")
        duplicate = FeatureDefinition("shared-release", "Second definition")

        with self.assertRaises(ValueError):
            validate_feature_definitions((first, duplicate))

    def test_registry_has_the_complete_deterministic_production_definitions(self) -> None:
        self.assertEqual(
            FEATURE_DEFINITIONS,
            (
                PAID_EVENTS,
                PAID_WATERMARKED_PREVIEWS,
                PAID_PHOTO_CART,
                PAID_PHOTO_PURCHASE,
                PAID_PHOTO_PAYMENT_SIMULATOR,
                BULK_PHOTO_DOWNLOAD,
            ),
        )
        self.assertEqual(
            tuple((definition.key, definition.description) for definition in FEATURE_DEFINITIONS),
            (
                ("paid-events", "Show paid events"),
                (
                    "paid-watermarked-previews",
                    "Show accepted paid watermarked previews",
                ),
                ("paid-photo-cart", "Allow paid photo cart selection"),
                ("paid-photo-purchase", "Allow paid photo checkout and fulfillment"),
                (
                    "paid-photo-payment-simulator",
                    "Use the feature-gated test payment screen",
                ),
                ("bulk-photo-download", "Allow page-scoped photo archive downloads"),
            ),
        )
