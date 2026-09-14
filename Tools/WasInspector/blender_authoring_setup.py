#!/usr/bin/env python3
"""Create a clean Blender authoring scene from the bundled RDR1 character rig."""

from __future__ import annotations

import argparse
import json
import math
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
    parser.add_argument("--preset", choices=("blank", "spider_leap"), default="blank")
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
    base = {bone.name: (bone.location.copy(), bone.rotation_quaternion.copy(), bone.scale.copy())
            for bone in armature.pose.bones}

    def restore_base() -> None:
        for bone in armature.pose.bones:
            location, rotation, scale = base[bone.name]
            bone.location = location
            bone.rotation_mode = "QUATERNION"
            bone.rotation_quaternion = rotation
            bone.scale = scale

    def key_all(frame: int) -> None:
        for bone in armature.pose.bones:
            bone.keyframe_insert("location", frame=frame)
            bone.keyframe_insert("rotation_quaternion", frame=frame)
            bone.keyframe_insert("scale", frame=frame)

    def pose(frame: int, *, root_y: float = 0.0, rotations: dict[str, tuple[float, float, float]] | None = None) -> None:
        restore_base()
        root = armature.pose.bones.get("character_root")
        if root:
            root.location.y += root_y
        for name, angles in (rotations or {}).items():
            bone = armature.pose.bones.get(name)
            if bone:
                delta = __import__("mathutils").Euler(tuple(math.radians(angle) for angle in angles), "XYZ").to_quaternion()
                bone.rotation_quaternion = bone.rotation_quaternion @ delta
        key_all(frame)

    if options.preset == "spider_leap":
        # Readable non-destructive sample: crouch, leap/reach, then land.
        pose(1)
        pose(max(2, round(options.frames * 0.24)), root_y=-0.12, rotations={
            "spine01": (12, 0, 0), "spine02": (18, 0, 0), "spine03": (22, 0, 0),
            "neck": (-8, 0, 0), "arm_l": (-28, 0, -16), "elbow_l": (-55, 0, 0),
            "arm_r": (-28, 0, 16), "elbow_r": (-55, 0, 0), "hip_l": (-18, 0, 0),
            "knee_l": (42, 0, 0), "hip_r": (-18, 0, 0), "knee_r": (42, 0, 0),
        })
        pose(max(3, round(options.frames * 0.52)), root_y=0.38, rotations={
            "spine01": (-8, 0, 0), "spine02": (-12, 0, 0), "spine03": (-14, 0, 0),
            "neck": (6, 0, 0), "arm_l": (-70, 0, -28), "elbow_l": (-18, 0, 0),
            "arm_r": (-70, 0, 28), "elbow_r": (-18, 0, 0), "hip_l": (22, 0, 0),
            "knee_l": (-28, 0, 0), "hip_r": (22, 0, 0), "knee_r": (-28, 0, 0),
        })
        pose(max(4, round(options.frames * 0.76)), root_y=-0.08, rotations={
            "spine01": (10, 0, 0), "spine02": (14, 0, 0), "spine03": (16, 0, 0),
            "neck": (-5, 0, 0), "arm_l": (-35, 0, -18), "elbow_l": (-48, 0, 0),
            "arm_r": (-35, 0, 18), "elbow_r": (-48, 0, 0), "hip_l": (-12, 0, 0),
            "knee_l": (35, 0, 0), "hip_r": (-12, 0, 0), "knee_r": (35, 0, 0),
        })
        pose(options.frames)
    else:
        restore_base()
        key_all(1)
        key_all(options.frames)
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
        "preset": options.preset,
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
