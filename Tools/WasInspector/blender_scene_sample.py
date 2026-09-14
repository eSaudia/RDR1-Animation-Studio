#!/usr/bin/env python3
"""Create a small Blender scene showing the planned RDR1 cinematic exporter workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import bpy
from mathutils import Vector


def options() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-blend", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    marker = __import__("sys").argv.index("--") + 1 if "--" in __import__("sys").argv else len(__import__("sys").argv)
    return parser.parse_args(__import__("sys").argv[marker:])


def main() -> int:
    args = options()
    bpy.ops.wm.open_mainfile(filepath=str(args.base_blend.resolve()))
    source_objects = list(bpy.context.scene.objects)
    source_armature = next((obj for obj in source_objects if obj.type == "ARMATURE"), None)
    if source_armature is None:
        raise RuntimeError("The bundled character scene has no armature.")

    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 720  # 24 seconds at 30 FPS
    scene.render.fps, scene.render.fps_base = 30, 1
    scene.world.color = (0.025, 0.035, 0.05)

    # Clone the bundled character twice. Each clone receives its own action and collection.
    actors = []
    for label, x, action_name in (
        ("John", -1.6, "intro_john_walk"),
        ("Deputy", 1.6, "intro_deputy_react"),
    ):
        collection = bpy.data.collections.new(label + " · Actor")
        scene.collection.children.link(collection)
        mapping = {}
        for source in source_objects:
            clone = source.copy()
            if source.data is not None:
                clone.data = source.data.copy()
            clone.name = label + " · " + source.name
            collection.objects.link(clone)
            mapping[source] = clone
        for source, clone in mapping.items():
            if source.parent is not None:
                clone.parent = mapping.get(source.parent)
            if clone.type == "MESH":
                for modifier in clone.modifiers:
                    if modifier.type == "ARMATURE":
                        modifier.object = mapping[source_armature]
        armature = mapping[source_armature]
        armature.location = (x, 0, 0)
        armature.animation_data_clear()
        action = bpy.data.actions.new(action_name)
        action.use_fake_user = True
        armature.animation_data_create()
        armature.animation_data.action = action
        root = armature.pose.bones.get("character_root")
        spine = armature.pose.bones.get("spine01")
        neck = armature.pose.bones.get("neck")
        # Keep the placeholder actors planted. Their hand-behind geometry is the
        # bundled bind pose; real WAS actions will replace these keys later.
        for frame, advance, lean in ((1, 0.0, 0.0), (180, 0.0, 0.0), (390, 0.0, -0.12), (540, 0.0, 0.06), (720, 0.0, 0.0)):
            if root:
                root.location = (0, advance, 0)
                root.keyframe_insert("location", frame=frame)
            if spine:
                spine.rotation_mode = "XYZ"
                spine.rotation_euler.x = lean if label == "John" else -lean
                spine.keyframe_insert("rotation_euler", frame=frame)
            if neck:
                neck.rotation_mode = "XYZ"
                neck.rotation_euler.z = 0.18 if label == "Deputy" and frame in (390, 540) else 0.0
                neck.keyframe_insert("rotation_euler", frame=frame)
        armature["rdr1_scene_actor"] = label
        actors.append({"name": label, "collection": collection.name, "action": action_name})

    for obj in source_objects:
        bpy.data.objects.remove(obj, do_unlink=True)

    # A simple stage makes the timeline readable immediately when the .blend opens.
    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 1, -0.03))
    ground = bpy.context.object
    ground.name = "Intro street · stage placeholder"
    ground["rdr1_scene_asset_status"] = "placeholder"
    bpy.ops.object.camera_add(location=(6.2, -9.5, 3.6))
    camera = bpy.context.object
    camera.name = "Intro camera · generated sample"
    camera.data.lens = 48
    target = Vector((0, 0.6, 1.1))
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    for name, location, energy, size in (
        ("Key light", (4, -4, 8), 1100, 5),
        ("Fill light", (-5, -1, 5), 650, 4),
    ):
        data = bpy.data.lights.new(name, "AREA")
        data.energy, data.shape, data.size = energy, "DISK", size
        light = bpy.data.objects.new(name, data)
        scene.collection.objects.link(light)
        light.location = location
        light.rotation_euler = (target - light.location).to_track_quat("-Z", "Y").to_euler()

    markers = ((1, "SHOT 01 · Establishing"), (121, "SHOT 02 · Push-in"), (271, "SHOT 03 · Cutaway"), (451, "SHOT 04 · Wide exit"))
    for frame, name in markers:
        scene.timeline_markers.new(name, frame=frame)
    manifest = {
        "format": "rdr1-animation-studio-scene-sample-v1",
        "scene": "intro_prototype",
        "fps": 30,
        "frames": 720,
        "actors": actors,
        "cameraShots": [{"frame": frame, "name": name} for frame, name in markers],
        "status": "prototype placeholder; not decoded mission data; actors use bundled bind pose",
    }
    scene["rdr1_scene_manifest"] = json.dumps(manifest, separators=(",", ":"))
    scene["rdr1_scene_export_note"] = "Generated by RDR1 Animation Studio Scene Exporter prototype. Replace placeholder actions with decoded clips."
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output.resolve()))
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
