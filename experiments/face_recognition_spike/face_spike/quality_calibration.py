from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import cv2

from .quality import FaceQualityThresholds, evaluate_quality_values


def build_quality_calibration(
    run: Path,
    cluster_quality_csv: Path,
    thresholds: FaceQualityThresholds,
) -> dict[str, Any]:
    thresholds.validate()
    faces_payload = json.loads((run / "faces.json").read_text(encoding="utf-8"))
    clusters_payload = json.loads((run / "clusters.json").read_text(encoding="utf-8"))
    labels = _load_labels(cluster_quality_csv)
    face_by_id: dict[str, dict[str, Any]] = {}
    for image in faces_payload["images"]:
        for face in image["faces"]:
            face_by_id[face["face_id"]] = {
                **face,
                "image_width": image["width"],
                "image_height": image["height"],
            }

    rows: list[dict[str, Any]] = []
    summary: Counter[str] = Counter()
    for cluster in clusters_payload["clusters"]:
        cluster_id = cluster["cluster_id"]
        label = labels.get(cluster_id)
        if label is None:
            continue
        decisions = [
            _evaluate_saved_face(run, face_by_id[member["face_id"]], thresholds)
            for member in cluster["members"]
        ]
        accepted = sum(decision.decision == "accepted" for decision in decisions)
        total = len(decisions)
        rows.append(
            {
                "cluster_id": cluster_id,
                "manual_quality": label,
                "total_faces": total,
                "accepted_faces": accepted,
                "fully_rejected": accepted == 0,
            }
        )
        summary[f"{label}_total"] += 1
        if label == "usable" and accepted:
            summary["usable_retained"] += 1
        elif label == "not_face" and not accepted:
            summary["not_face_rejected"] += 1
        elif label == "low_quality" and not accepted:
            summary["low_quality_fully_rejected"] += 1

    return {
        "thresholds": {
            "minimum_confidence": thresholds.minimum_confidence,
            "minimum_face_px": thresholds.minimum_face_px,
            "minimum_relative_area": thresholds.minimum_relative_area,
            "minimum_sharpness": thresholds.minimum_sharpness,
        },
        "summary": {
            "reviewed_clusters": len(rows),
            "usable_retained": summary["usable_retained"],
            "usable_total": summary["usable_total"],
            "not_face_rejected": summary["not_face_rejected"],
            "not_face_total": summary["not_face_total"],
            "low_quality_fully_rejected": summary["low_quality_fully_rejected"],
            "low_quality_total": summary["low_quality_total"],
        },
        "clusters": rows,
    }


def _evaluate_saved_face(
    run: Path,
    face: Mapping[str, Any],
    thresholds: FaceQualityThresholds,
) -> Any:
    crop_path = run / str(face["crop_path"])
    if crop_path.parent.resolve() != (run / "faces").resolve():
        raise ValueError("unsafe face crop path")
    grayscale = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if grayscale is None:
        raise ValueError("face crop cannot be decoded")
    sharpness = float(cv2.Laplacian(grayscale, cv2.CV_64F).var())
    return evaluate_quality_values(
        confidence=float(face["confidence"]),
        minimum_side_px=min(float(face["width"]), float(face["height"])),
        relative_area=(
            float(face["width"])
            * float(face["height"])
            / (int(face["image_width"]) * int(face["image_height"]))
        ),
        sharpness=sharpness,
        thresholds=thresholds,
    )


def _load_labels(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = tuple(csv.DictReader(stream))
    return {row["cluster_id"]: row["quality"] for row in rows if row["quality"] != "unreviewed"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--cluster-quality-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-confidence", type=float, default=0.82)
    parser.add_argument("--minimum-face-px", type=int, default=32)
    parser.add_argument("--minimum-relative-area", type=float, default=0.0009)
    parser.add_argument("--minimum-sharpness", type=float, default=50.0)
    arguments = parser.parse_args(argv)
    if os.path.lexists(arguments.output):
        parser.error(f"output path already exists: {arguments.output}")
    thresholds = FaceQualityThresholds(
        arguments.minimum_confidence,
        arguments.minimum_face_px,
        arguments.minimum_relative_area,
        arguments.minimum_sharpness,
    )
    result = build_quality_calibration(arguments.run, arguments.cluster_quality_csv, thresholds)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{arguments.output.name}.",
        suffix=".tmp",
        dir=arguments.output.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, arguments.output)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
