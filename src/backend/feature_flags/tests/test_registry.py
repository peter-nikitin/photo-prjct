from dataclasses import FrozenInstanceError

from django.test import SimpleTestCase

from feature_flags.registry import FeatureDefinition, validate_feature_definitions


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
