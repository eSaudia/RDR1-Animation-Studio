"""Join decoded WSI placement instances to WVD visual-dictionary hashes.

This step deliberately joins only on the engine's stored 32-bit drawable hash.
It does not infer names, material bindings, room membership, or mission
ownership.  The result is a compact, inspectable placement index that can be
used by the scene exporter once terrain/material decoding is available.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _hash_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value & 0xFFFFFFFF
    if isinstance(value, str):
        try:
            return int(value, 0) & 0xFFFFFFFF
        except ValueError:
            return None
    return None


def _load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def build(package: Path) -> dict[str, Any]:
    world = package / "world-cell"
    wsi_reports = sorted(world.rglob("*.wsi.semantics.json"))
    if not wsi_reports:
        raise FileNotFoundError("No decoded *.wsi.semantics.json report exists in world-cell")
    wsi_path = wsi_reports[0]
    wsi = _load(wsi_path)
    if not wsi or not isinstance(wsi.get("decoded"), dict):
        raise ValueError(f"WSI report has no decoded object: {wsi_path}")

    visual_hashes: dict[int, list[dict[str, Any]]] = {}
    dictionaries: list[dict[str, Any]] = []
    for report_path in sorted(world.rglob("*.wvd.semantics.json")):
        report = _load(report_path)
        decoded = report.get("decoded") if report else None
        if not isinstance(decoded, dict):
            continue
        hashes = decoded.get("hashes")
        if not isinstance(hashes, list):
            continue
        relative = report_path.relative_to(package).as_posix()
        dictionary = {
            "report": relative,
            "source": report.get("source", ""),
            "hashCount": len(hashes),
        }
        dictionaries.append(dictionary)
        drawables = decoded.get("drawables")
        for index, raw_hash in enumerate(hashes):
            hash_value = _hash_value(raw_hash)
            if hash_value is None:
                continue
            drawable: dict[str, Any] = {
                "report": relative,
                "source": report.get("source", ""),
                "drawableIndex": index,
                "hash": f"0x{hash_value:08X}",
            }
            if isinstance(drawables, list) and index < len(drawables) and isinstance(drawables[index], dict):
                item = drawables[index]
                for key in ("pointer", "targetOffset", "status"):
                    if key in item:
                        drawable[key] = item[key]
            visual_hashes.setdefault(hash_value, []).append(drawable)

    decoded = wsi["decoded"]
    raw_instances = decoded.get("instances")
    instances: list[dict[str, Any]] = []
    if isinstance(raw_instances, list):
        for raw in raw_instances:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            hash_value = _hash_value(raw.get("instanceHash"))
            matches = visual_hashes.get(hash_value, []) if hash_value is not None else []
            item["visualMatches"] = matches
            item["matchStatus"] = "matched-by-hash" if matches else "unmatched-hash"
            instances.append(item)

    matched = sum(1 for item in instances if item.get("matchStatus") == "matched-by-hash")
    unresolved = [
        "WSI secondary drawable-instance records and room/portal relationships remain unresolved",
        "WVD shader/material bindings, terrain, and streamed-cell ownership remain unresolved",
        "hash equality identifies visual dictionary candidates but does not prove mission ownership",
    ]
    return {
        "format": "rdr1-world-cell-placement-join-v0.25",
        "confidence": "WSI placement matrices are pointer/range validated; visual candidates are joined only by exact 32-bit drawable hash",
        "wsiReport": wsi_path.relative_to(package).as_posix(),
        "visualDictionaries": dictionaries,
        "counts": {
            "instances": len(instances),
            "matchedInstances": matched,
            "unmatchedInstances": len(instances) - matched,
            "visualHashes": len(visual_hashes),
        },
        "instances": instances,
        "unresolved": unresolved,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(args.package.resolve())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(result["counts"], separators=(",", ":")))
        return 0
    except Exception as exc:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"format": "rdr1-world-cell-placement-join-v0.25", "status": "join-failed", "error": str(exc)}, indent=2), encoding="utf-8")
        print(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
