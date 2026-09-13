#!/usr/bin/env python3
"""Bind numeric WAS animation tracks to an RDR1 skeleton mapping."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def load_skeleton(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    model = next((line[8:] for line in lines if line.startswith("# model=")), None)
    reader = csv.DictReader(line for line in lines if not line.startswith("#"))
    bones: list[dict[str, Any]] = []
    for row in reader:
        bones.append(
            {
                "index": int(row["index"]),
                "bone_id": int(row["bone_id"]),
                "name": row["name"],
                "parent_index": int(row["parent_index"]),
                "parent_name": row["parent_name"] or None,
                "position": [float(row[f"pos_{axis}"]) for axis in "xyz"],
                "rotation_xyzw": [float(row[f"rot_{axis}"]) for axis in "xyzw"],
            }
        )
    return model, bones


def bind(animation: dict[str, Any], model: str | None, bones: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {bone["bone_id"]: bone for bone in bones}
    used: set[int] = set()
    unmatched: set[int] = set()

    for entry in animation.get("entries", []):
        for channel in entry.get("channels", []):
            bone_id = int(channel["bone_id"])
            bone = by_id.get(bone_id)
            if bone is None:
                unmatched.add(bone_id)
                channel["skeleton_binding"] = None
                continue
            used.add(bone_id)
            channel["skeleton_binding"] = {
                "index": bone["index"],
                "name": bone["name"],
                "parent_index": bone["parent_index"],
                "parent_name": bone["parent_name"],
                "bind_position": bone["position"],
                "bind_rotation_xyzw": bone["rotation_xyzw"],
            }

    animation["schema"] = "rdr1-animation-studio.was-animation-export.v3"
    animation["skeleton"] = {
        "model": model,
        "bone_count": len(bones),
        "bones": bones,
    }
    animation["binding_coverage"] = {
        "animation_bone_ids": len(used | unmatched),
        "matched_bone_ids": len(used),
        "unmatched_bone_ids": sorted(unmatched),
        "complete": not unmatched,
    }
    return animation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("animation_json", type=Path)
    parser.add_argument("skeleton_csv", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()

    animation = json.loads(args.animation_json.read_text(encoding="utf-8"))
    model, bones = load_skeleton(args.skeleton_csv)
    result = bind(animation, model, bones)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    coverage = result["binding_coverage"]
    print(
        f"Bound {coverage['matched_bone_ids']}/{coverage['animation_bone_ids']} animation bone IDs "
        f"to {len(bones)} skeleton bones."
    )
    return 0 if coverage["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
