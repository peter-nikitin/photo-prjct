#!/usr/bin/env python3
"""Refresh FindMe's native Unified Agent objects without removing unrelated routes."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Any

import yaml

NATIVE_STORAGE = "metrics_buffer"
NATIVE_CHANNEL = "cloud_monitoring"


def _document(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Unified Agent configuration must be a mapping")
    for section in ("storages", "channels", "routes"):
        items = value.get(section, [])
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError(f"Unified Agent {section} must be a list of mappings")
    return value


def merge_native(current: dict[str, Any], native: dict[str, Any]) -> dict[str, Any]:
    current = _document(current)
    native = _document(native)
    result = copy.deepcopy(current)
    result["status"] = copy.deepcopy(native.get("status", {}))
    for section, owned_name in (
        ("storages", NATIVE_STORAGE),
        ("channels", NATIVE_CHANNEL),
    ):
        owned = [item for item in native[section] if item.get("name") == owned_name]
        if len(owned) != 1:
            raise ValueError(f"native {section} ownership is invalid")
        result[section] = [
            copy.deepcopy(item) for item in current[section] if item.get("name") != owned_name
        ] + copy.deepcopy(owned)
    native_routes = [
        route
        for route in native["routes"]
        if route.get("channel", {}).get("channel_ref", {}).get("name") == NATIVE_CHANNEL
    ]
    if len(native_routes) != len(native["routes"]):
        raise ValueError("native route ownership is invalid")
    result["routes"] = [
        copy.deepcopy(route)
        for route in current["routes"]
        if route.get("channel", {}).get("channel_ref", {}).get("name") != NATIVE_CHANNEL
    ] + copy.deepcopy(native_routes)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    current = yaml.safe_load(args.current.read_text(encoding="utf-8"))
    native = yaml.safe_load(args.native.read_text(encoding="utf-8"))
    merged = merge_native(current, native)
    args.output.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    main()
