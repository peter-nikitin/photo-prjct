"""Pure lifecycle-document construction for the approval-gated storage runbook."""

from __future__ import annotations

from copy import deepcopy

SELFIE_SEARCH_LIFECYCLE_RULE_ID = "selfie-search-expire-after-24h"
SELFIE_SEARCH_TEMPORARY_PREFIX = "selfie-search/"


def build_selfie_search_lifecycle_configuration(
    existing_configuration: object | None,
) -> dict[str, list[dict[str, object]]]:
    """Append the exact temporary-prefix rule without mutating or dropping existing rules."""
    rules = _copied_rules(existing_configuration)
    if any(
        rule.get("ID") == SELFIE_SEARCH_LIFECYCLE_RULE_ID
        or _prefix(rule) == SELFIE_SEARCH_TEMPORARY_PREFIX
        for rule in rules
    ):
        raise ValueError("selfie-search lifecycle rule already exists")
    rules.append(
        {
            "ID": SELFIE_SEARCH_LIFECYCLE_RULE_ID,
            "Status": "Enabled",
            "Filter": {"Prefix": SELFIE_SEARCH_TEMPORARY_PREFIX},
            "Expiration": {"Days": 1},
        }
    )
    return {"Rules": rules}


def _copied_rules(existing_configuration: object | None) -> list[dict[str, object]]:
    if existing_configuration is None:
        return []
    if not isinstance(existing_configuration, dict):
        raise ValueError("invalid lifecycle configuration")
    rules = existing_configuration.get("Rules")
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise ValueError("invalid lifecycle rules")
    return deepcopy(rules)


def _prefix(rule: dict[str, object]) -> object:
    filter_value = rule.get("Filter")
    if isinstance(filter_value, dict):
        return filter_value.get("Prefix")
    return rule.get("Prefix")
