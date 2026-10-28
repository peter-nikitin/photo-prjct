"""Print only resource addresses and actions from a Terraform plan JSON."""

import json
import sys


def summarize(data: dict) -> list[str]:
    lines = []
    for change in data.get("resource_changes", []):
        address = change["address"]
        actions = ",".join(change["change"]["actions"])
        lines.append(f"{actions}: {address}")
    return sorted(lines)


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as source:
        data = json.load(source)
    print("\n".join(summarize(data)) or "No resource changes")


if __name__ == "__main__":
    main()
