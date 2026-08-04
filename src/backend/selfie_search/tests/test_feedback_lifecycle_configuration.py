from django.test import SimpleTestCase
from selfie_search.feedback_lifecycle import build_feedback_lifecycle_configuration


class FeedbackLifecycleConfigurationTests(SimpleTestCase):
    def test_builds_the_exact_whole_bucket_thirty_day_document(self) -> None:
        configuration = build_feedback_lifecycle_configuration(None)

        self.assertEqual(
            configuration,
            {
                "Rules": [
                    {
                        "ID": "selfie-feedback-expire-after-30d",
                        "Status": "Enabled",
                        "Filter": {"Prefix": ""},
                        "Expiration": {"Days": 30},
                    }
                ]
            },
        )

    def test_rejects_an_existing_rule_in_the_dedicated_feedback_bucket(self) -> None:
        with self.assertRaises(ValueError):
            build_feedback_lifecycle_configuration(
                {
                    "Rules": [
                        {
                            "ID": "unrelated-expiry",
                            "Status": "Enabled",
                            "Filter": {"Prefix": "other/"},
                            "Expiration": {"Days": 1},
                        }
                    ]
                }
            )
