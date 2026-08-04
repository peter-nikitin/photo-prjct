"""Exact lifecycle document for the dedicated consented-feedback bucket."""

from __future__ import annotations

SELFIE_FEEDBACK_LIFECYCLE_RULE_ID = "selfie-feedback-expire-after-30d"


def build_feedback_lifecycle_configuration(
    existing_configuration: object | None,
) -> dict[str, list[dict[str, object]]]:
    """Return the only lifecycle document allowed in the dedicated feedback bucket."""
    if existing_configuration is not None:
        if not isinstance(existing_configuration, dict):
            raise ValueError("invalid lifecycle configuration")
        rules = existing_configuration.get("Rules")
        if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
            raise ValueError("invalid lifecycle rules")
        if rules:
            raise ValueError("feedback lifecycle rule collision")
    return {
        "Rules": [
            {
                "ID": SELFIE_FEEDBACK_LIFECYCLE_RULE_ID,
                "Status": "Enabled",
                "Filter": {"Prefix": ""},
                "Expiration": {"Days": 30},
            }
        ]
    }
