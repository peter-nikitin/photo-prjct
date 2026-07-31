from django.test import SimpleTestCase
from selfie_search.lifecycle import build_selfie_search_lifecycle_configuration


class SelfieSearchLifecycleConfigurationTests(SimpleTestCase):
    def test_preserves_every_existing_rule_when_adding_exact_selfie_expiration_rule(self) -> None:
        existing = {
            "Rules": [
                {
                    "ID": "incoming-expire",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "incoming/"},
                    "Expiration": {"Days": 1},
                },
                {
                    "ID": "original-archive",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "originals/"},
                    "Transitions": [{"Days": 30, "StorageClass": "COLD"}],
                },
            ]
        }

        configuration = build_selfie_search_lifecycle_configuration(existing)

        self.assertEqual(configuration["Rules"][:2], existing["Rules"])
        self.assertEqual(
            configuration["Rules"][2],
            {
                "ID": "selfie-search-expire-after-24h",
                "Status": "Enabled",
                "Filter": {"Prefix": "selfie-search/"},
                "Expiration": {"Days": 1},
            },
        )
        self.assertEqual(existing["Rules"], existing["Rules"])

    def test_rejects_existing_selfie_rule_id_or_prefix_collision(self) -> None:
        for rule in (
            {"ID": "selfie-search-expire-after-24h", "Filter": {"Prefix": "other/"}},
            {"ID": "other", "Filter": {"Prefix": "selfie-search/"}},
        ):
            with self.subTest(rule=rule):
                with self.assertRaises(ValueError):
                    build_selfie_search_lifecycle_configuration({"Rules": [rule]})

    def test_builds_single_exact_rule_when_no_lifecycle_configuration_exists(self) -> None:
        configuration = build_selfie_search_lifecycle_configuration(None)

        self.assertEqual(len(configuration["Rules"]), 1)
        self.assertEqual(configuration["Rules"][0]["Filter"], {"Prefix": "selfie-search/"})
