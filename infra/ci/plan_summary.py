"""Print only resource addresses and actions from a Terraform plan JSON."""

import json
import sys

CDN_TYPES = {"findmecdn_endpoint", "findmecdn_rule", "yandex_cdn_origin_group"}


def require_import_only(data: dict) -> None:
    changes = data.get("resource_changes", [])
    importing_cdn = any(
        change.get("type") in CDN_TYPES and "importing" in change["change"] for change in changes
    )
    if not importing_cdn:
        return
    mutations = [
        change["address"]
        for change in changes
        if change.get("mode", "managed") == "managed" and change["change"]["actions"] != ["no-op"]
    ]
    if mutations:
        raise ValueError(f"CDN import must only record existing resources: {', '.join(mutations)}")


def cdn_only(data: dict) -> bool:
    changes = [
        change
        for change in data.get("resource_changes", [])
        if change.get("mode", "managed") == "managed"
        and ("importing" in change["change"] or change["change"]["actions"] != ["no-op"])
    ]
    return bool(changes) and all(change.get("type") in CDN_TYPES for change in changes)


def summarize(data: dict) -> list[str]:
    require_import_only(data)
    lines = []
    for change in data.get("resource_changes", []):
        address = change["address"]
        actions = ",".join(change["change"]["actions"])
        if "importing" in change["change"]:
            actions = "import" if actions == "no-op" else f"import,{actions}"
        lines.append(f"{actions}: {address}")
    return sorted(lines)


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as source:
        data = json.load(source)
    try:
        lines = summarize(data)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    if sys.argv[2:] == ["--cdn-only"]:
        print("true" if cdn_only(data) else "false")
    else:
        print("\n".join(lines) or "No resource changes")


if __name__ == "__main__":
    main()
