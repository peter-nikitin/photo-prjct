import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_queries_do_not_include_private_identity_labels() -> None:
    import re

    dashboard = json.loads((ROOT / "deploy/monitoring/dashboard.json").read_text())
    queries = [
        target["query"]
        for widget in dashboard["widgets"]
        if "chart" in widget
        for target in widget["chart"]["queries"]["targets"]
    ]
    assert queries
    for query in queries:
        for label in (
            "email",
            "token",
            "user_id",
            "photo_id",
            "order_id",
            "payment_id",
            "session_id",
        ):
            assert not re.search(rf"\b{label}\s*(?:!?=|!~)", query)
