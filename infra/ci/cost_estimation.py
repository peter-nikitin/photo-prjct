"""Adapt Terraform plan JSON to the pinned upstream estimator's input shape."""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SUPPORTED_RESOURCES = {
    "yandex_compute_instance",
    "yandex_compute_disk",
    "yandex_compute_filesystem",
    "yandex_compute_instance_group",
    "yandex_vpc_address",
}

_TOTAL_SECTIONS = {
    "CURRENT INFRASTRUCTURE:": "Сейчас",
    "PLANNED INFRASTRUCTURE:": "По плану",
    "DIFFERENCE:": "Разница",
}


def format_summary(report: str) -> str:
    """Show checked upstream totals in a compact format suitable for a PR comment."""
    totals: dict[str, tuple[str, str]] = {}
    section: str | None = None
    for line in report.splitlines():
        heading = line.strip()
        if heading in _TOTAL_SECTIONS:
            section = heading
            continue
        if section is None:
            continue
        match = re.fullmatch(r"\s*(Hourly|Monthly):\s*([+-]?[\d,]+(?:\.\d+)?)\s+RUB\s*", line)
        if match:
            hourly, monthly = totals.get(section, ("", ""))
            value = match.group(2).replace(",", "")
            totals[section] = (
                value if match.group(1) == "Hourly" else hourly,
                value if match.group(1) == "Monthly" else monthly,
            )
        elif heading.startswith("=== "):
            section = None

    if set(totals) != set(_TOTAL_SECTIONS) or any(
        not hourly or not monthly for hourly, monthly in totals.values()
    ):
        raise RuntimeError("upstream cost estimator returned missing or invalid cost totals")

    def display(value: str) -> str:
        integer, dot, fraction = value.partition(".")
        sign = integer[0] if integer.startswith(("+", "-")) else ""
        grouped = f"{int(integer.lstrip('+-')):,}".replace(",", " ")
        return f"{sign}{grouped}{dot}{fraction.rstrip('0') if dot else ''}".rstrip(".")

    lines = []
    for heading, label in _TOTAL_SECTIONS.items():
        hourly, monthly = totals[heading]
        lines.append(f"- {label}: {display(monthly)} RUB/мес. ({display(hourly)} RUB/ч)")
    lines.append("- Расчётная база: 744 ч/мес.")
    return "\n".join(lines) + "\n"


def _resource_address_from_reference(
    references: list[str], addresses: set[str], disk_id: str | None, disk_by_address: dict
) -> str:
    matches = {
        address for address in addresses if address in references or f"{address}.id" in references
    }
    if not matches and disk_id:
        matches = {
            address
            for address, resource in disk_by_address.items()
            if resource.get("values", {}).get("id") == disk_id
        }
    if len(matches) != 1:
        raise ValueError("cannot resolve the VM boot disk reference in Terraform configuration")
    return matches.pop()


def _normalize_root_module(root: dict[str, Any], config_resources: dict[str, Any]) -> None:
    resources = root.get("resources", [])
    disk_by_address = {
        resource["address"]: resource
        for resource in resources
        if resource.get("type") == "yandex_compute_disk"
    }
    attached_boot_disks: set[str] = set()

    for resource in resources:
        if resource.get("type") != "yandex_compute_instance":
            continue
        values = resource.get("values", {})
        if values.get("scheduling_policy") is None:
            values["scheduling_policy"] = [{}]
        boot_disks = values.get("boot_disk") or []
        if not boot_disks:
            continue

        initialize_params = boot_disks[0].get("initialize_params")
        if (
            isinstance(initialize_params, list)
            and initialize_params
            and initialize_params[0].get("size", 0) > 0
            and initialize_params[0].get("type")
        ):
            continue

        configuration = config_resources.get(resource.get("address"), {})
        boot_expressions = configuration.get("expressions", {}).get("boot_disk") or []
        references = (
            boot_expressions[0].get("disk_id", {}).get("references", []) if boot_expressions else []
        )
        disk_address = _resource_address_from_reference(
            references,
            set(disk_by_address),
            boot_disks[0].get("disk_id"),
            disk_by_address,
        )
        if disk_address in attached_boot_disks:
            raise ValueError("Terraform plan references one boot disk from multiple VMs")
        disk_resource = disk_by_address[disk_address]
        disk_values = disk_resource.get("values", {})
        disk_size = disk_values.get("size")
        disk_type = disk_values.get("type")
        if not isinstance(disk_size, (int, float)) or disk_size <= 0:
            raise ValueError("referenced VM boot disk has no known positive size")
        if not isinstance(disk_type, str) or not disk_type:
            raise ValueError("referenced VM boot disk has no known type")

        boot_disks[0]["initialize_params"] = [{"size": disk_size, "type": disk_type}]
        attached_boot_disks.add(disk_address)

    if attached_boot_disks:
        root["resources"] = [
            resource for resource in resources if resource.get("address") not in attached_boot_disks
        ]


def normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Represent attached standalone boot disks in the upstream's inline shape."""
    normalized = copy.deepcopy(plan)
    config_resources = {
        resource.get("address"): resource
        for resource in normalized.get("configuration", {})
        .get("root_module", {})
        .get("resources", [])
    }
    prior_root = normalized.get("prior_state", {}).get("values", {}).get("root_module")
    if prior_root is not None:
        _normalize_root_module(prior_root, config_resources)
    planned_root = normalized.get("planned_values", {}).get("root_module")
    if planned_root is not None:
        _normalize_root_module(planned_root, config_resources)
    return normalized


def run_estimator(estimator_dir: Path, plan_path: Path) -> str:
    """Run upstream and reject its CLI's caught-and-printed errors and empty reports."""
    result = subprocess.run(
        [
            sys.executable,
            "app.py",
            str(plan_path),
            "--full",
            "--no-color",
        ],
        cwd=estimator_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout
    if result.returncode != 0:
        raise RuntimeError("upstream cost estimator exited with an error")
    if any(line.startswith("Error:") for line in output.splitlines()):
        raise RuntimeError("upstream cost estimator reported an error")
    if not all(
        marker in output
        for marker in (
            "=== TERRAFORM COST ESTIMATION ===",
            "PLANNED INFRASTRUCTURE:",
            "=== CURRENT USAGE DETAILS ===",
            "=== PLANNED USAGE DETAILS ===",
        )
    ):
        raise RuntimeError("upstream cost estimator returned an incomplete report")
    return output


def estimate(plan_path: Path, estimator_dir: Path) -> str:
    with plan_path.open() as source:
        normalized = normalize_plan(json.load(source))

    with tempfile.TemporaryDirectory(prefix="findme-cost-plan-") as temp_dir:
        normalized_path = Path(temp_dir) / "plan.json"
        normalized_path.write_text(json.dumps(normalized))
        output = run_estimator(estimator_dir, normalized_path)

    for section_key, details_key in (
        ("prior_state.values.root_module.resources", "=== CURRENT USAGE DETAILS ==="),
        ("planned_values.root_module.resources", "=== PLANNED USAGE DETAILS ==="),
    ):
        section: dict[str, Any] = normalized
        for key in section_key.split("."):
            section = section.get(key, {})
        details = output.split(details_key, 1)[1]
        if details_key == "=== CURRENT USAGE DETAILS ===":
            details = details.split("=== PLANNED USAGE DETAILS ===", 1)[0]
        for resource in section if isinstance(section, list) else []:
            if resource.get("type") not in SUPPORTED_RESOURCES:
                continue
            name = resource.get("name")
            resource_type = resource.get("type")
            if name not in details or resource_type not in details:
                state = "current" if details_key == "=== CURRENT USAGE DETAILS ===" else "planned"
                raise RuntimeError(f"upstream cost estimator omitted a supported {state} resource")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--estimator-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        sys.stdout.write(format_summary(estimate(args.plan, args.estimator_dir)))
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"Cost estimation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
