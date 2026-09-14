#!/usr/bin/env python3
"""Create a clean Blender authoring scene from the bundled RDR1 character rig."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-blend", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--fps", type=float, required=True)
    marker = sys.argv.index("--") + 1 if "--" in sys.argv else len(sys.argv)
    return parser.parse_args(sys.argv[marker:])


def main() -> int:
    options = args()
    if options.frames < 2 or options.frames > 10_000:
        raise ValueError("Frame count must be between 2 and 10,000.")
    if options.fps <= 0 or options.fps > 240:
        raise ValueError("FPS must be greater than 0 and no more than 240.")
    bpy.ops.wm.open_mainfile(filepath=str(options.base_blend.resolve()))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    if not armatures:
        raise RuntimeError("The bundled Blender file has no armature.")
    armature = next((obj for obj in armatures if obj.name.lower() == "armature"), armatures[0])
    armature.hide_viewport = False
    armature.hide_render = False
    armature.hide_set(False)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    armature.animation_data_clear()
    action = bpy.data.actions.new(options.name)
    action.use_fake_user = True
    armature.animation_data_create()
    armature.animation_data.action = action
    for bone in armature.pose.bones:
        bone.rotation_mode = "QUATERNION"
        bone.keyframe_insert("location", frame=1)
        bone.keyframe_insert("rotation_quaternion", frame=1)
        bone.keyframe_insert("scale", frame=1)
        bone.keyframe_insert("location", frame=options.frames)
        bone.keyframe_insert("rotation_quaternion", frame=options.frames)
        bone.keyframe_insert("scale", frame=options.frames)
    scene = bpy.context.scene
    scene.render.fps = max(1, round(options.fps))
    scene.render.fps_base = scene.render.fps / options.fps
    scene.frame_start = 1
    scene.frame_end = options.frames
    scene.frame_set(1)
    metadata = {
        "format": "rdr1-animation-authoring-v1",
        "action": options.name,
        "frames": options.frames,
        "fps": options.fps,
        "source": "RDR1 Animation Studio bundled human rig",
        "note": "Edit this action in Blender. Existing WAS import remains fixed-layout experimental.",
    }
    scene["rdr1_animation_authoring"] = json.dumps(metadata, separators=(",", ":"))
    armature["rdr1_authoring_action"] = options.name
    options.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(options.output.resolve()))
    print(json.dumps(metadata))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
