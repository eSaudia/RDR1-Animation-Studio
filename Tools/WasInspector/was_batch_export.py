#!/usr/bin/env python3
"""Catalog or batch-export RDR1 WAS clips into one multi-action glTF/Blender file."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

import was_animation_export
import was_gltf_export
import was_inspector
import was_skeleton_bind


def catalog(path: pathlib.Path) -> tuple[bytes, list[dict]]:
    _, resource_type, _, _, decoded = was_inspector.load_rsc5(path)
    if resource_type != 6:
        raise was_inspector.InspectionError(
            f"Expected animation-set resource type 6, found {resource_type}.")
    records = was_inspector.scan_animation_source_records(decoded)
    return decoded, records


def public_record(record: dict) -> dict:
    return {key: record[key] for key in (
        "name", "source_path", "frame_count", "duration_seconds", "inferred_fps",
        "track_data_offset_hex", "encoded_size") if key in record}


def select_records(records: list[dict], filters: list[str], limit: int,
                   exact_names: set[str] | None = None) -> list[dict]:
    selected = records
    if exact_names is not None:
        selected = [record for record in records if record["name"].casefold() in exact_names]
    if filters:
        folded = [value.casefold() for value in filters]
        selected = [record for record in records
                    if any(value in record["name"].casefold() for value in folded)]
    if limit:
        selected = selected[:limit]
    return selected


def create_blend(blender: pathlib.Path, gltf: pathlib.Path, blend: pathlib.Path,
                 base_blend: pathlib.Path | None = None) -> None:
    expression = "\n".join((
        "import bpy",
        "bpy.ops.object.select_all(action='SELECT') if not " + repr(bool(base_blend)) + " else None",
        "bpy.ops.object.delete(use_global=False) if not " + repr(bool(base_blend)) + " else None",
        "bpy.context.scene.render.fps = 30",
        "before = set(bpy.data.objects)",
        "before_actions = set(bpy.data.actions)",
        "base_armatures = [o for o in before if o.type == 'ARMATURE']",
        "bpy.ops.import_scene.gltf(filepath=" + repr(str(gltf.resolve())) + ")",
        "imported = [o for o in bpy.data.objects if o not in before]",
        "actions = [a for a in bpy.data.actions if a not in before_actions]",
        "import json",
        "studio_records = json.load(open(" + repr(str(gltf.resolve())) + ", encoding='utf-8')).get('extras', {}).get('studioRecords', {})",
        "[(a.__setitem__('rdr1_source_sha256', studio_records[a.name]['source_sha256']), a.__setitem__('rdr1_record_offset', studio_records[a.name]['record_offset'])) for a in actions if a.name in studio_records]",
        "[(setattr(a, 'use_fake_user', True)) for a in actions]",
        "target = base_armatures[0] if base_armatures else next((o for o in imported if o.type == 'ARMATURE'), None)",
        "target.animation_data_create() if target else None",
        "setattr(target.animation_data, 'use_nla', False) if target else None",
        "setattr(target.animation_data, 'action', actions[0]) if target and actions else None",
        "setattr(target.animation_data, 'action_slot', actions[0].slots[0]) if target and actions and actions[0].slots else None",
        "bpy.ops.object.select_all(action='DESELECT')",
        "[(o.select_set(True)) for o in imported] if base_armatures else None",
        "bpy.ops.object.delete(use_global=False) if base_armatures and imported else None",
        "s = bpy.context.scene",
        "s.frame_start = 0",
        "s.frame_end = int(actions[0].frame_range[1]) if actions else 1",
        "s.sync_mode = 'FRAME_DROP'",
        "s.frame_set(0)",
        "bpy.ops.wm.save_as_mainfile(filepath=" + repr(str(blend.resolve())) + ")",
    ))
    command = [str(blender), "--background"]
    if base_blend:
        command.append(str(base_blend.resolve()))
    else:
        command.append("--factory-startup")
    command.extend(["--python-exit-code", "1", "--python-expr", expression])
    result = subprocess.run(command, check=False)
    if result.returncode:
        raise RuntimeError(f"Blender exited with code {result.returncode}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=pathlib.Path)
    parser.add_argument("--catalog", type=pathlib.Path, help="Write searchable clip metadata JSON.")
    parser.add_argument("--skeleton", type=pathlib.Path, help="RDR1 skeleton mapping for animation export.")
    parser.add_argument("-o", "--output", type=pathlib.Path, help="Multi-animation embedded glTF output.")
    parser.add_argument("--json-folder", type=pathlib.Path,
                        help="Export each selected clip as an individual decoded JSON file.")
    parser.add_argument("--filter", action="append", default=[], help="Case-insensitive clip-name filter (repeatable).")
    parser.add_argument("--names-file", type=pathlib.Path,
                        help="UTF-8 file containing exact clip names, one per line.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum selected clips; zero exports all matches.")
    parser.add_argument("--blender", type=pathlib.Path, help="Optional Blender executable.")
    parser.add_argument("--blend-output", type=pathlib.Path, help="Optional .blend output (requires --blender).")
    parser.add_argument("--base-blend", type=pathlib.Path,
                        help="Existing character .blend to receive the Actions (requires --blender).")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be zero or greater")
    if bool(args.blender) != bool(args.blend_output):
        parser.error("--blender and --blend-output must be supplied together")
    if args.base_blend and not args.blender:
        parser.error("--base-blend requires --blender and --blend-output")
    if args.output and not args.skeleton:
        parser.error("--output requires --skeleton")

    try:
        decoded, records = catalog(args.input)
        exact_names = None
        if args.names_file:
            exact_names = {line.strip().casefold() for line in
                           args.names_file.read_text(encoding="utf-8-sig").splitlines() if line.strip()}
        selected = select_records(records, args.filter, args.limit, exact_names)
        if args.catalog:
            args.catalog.parent.mkdir(parents=True, exist_ok=True)
            args.catalog.write_text(json.dumps({
                "schema": "rdr1-animation-studio.was-catalog.v1", "source": str(args.input.resolve()),
                "clip_count": len(records), "clips": [public_record(record) for record in records],
            }, indent=2) + "\n", encoding="utf-8")
        if args.output or args.json_folder:
            if not selected:
                raise was_inspector.InspectionError("No animation clips matched the selection.")
            model = bones = None
            if args.output:
                model, bones = was_skeleton_bind.load_skeleton(args.skeleton)
            if args.json_folder:
                args.json_folder.mkdir(parents=True, exist_ok=True)
            bound = []
            for index, record in enumerate(selected, 1):
                print(f"[{index}/{len(selected)}] {record['name']}", file=sys.stderr, flush=True)
                animation = was_animation_export.export_animation(
                    args.input, record["name"], 0, decoded_data=decoded, records=records)
                if args.json_folder:
                    safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', record["name"]).strip('._') or f"clip_{index}"
                    (args.json_folder / f"{safe_name}.animation.json").write_text(
                        json.dumps(animation, indent=2) + "\n", encoding="utf-8")
                if args.output:
                    bound.append(was_skeleton_bind.bind(animation, model, bones))
            if args.output:
                document = was_gltf_export.export_gltf_batch(bound)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")
                if args.blender:
                    create_blend(args.blender, args.output, args.blend_output, args.base_blend)
        print(f"clips={len(records)} selected={len(selected)}")
        return 0
    except (OSError, RuntimeError, ValueError, was_inspector.InspectionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
