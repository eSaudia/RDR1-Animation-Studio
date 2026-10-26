#!/usr/bin/env python3
"""Create a self-contained Blender scene from a merged RDR1 character glTF.

Unlike the legacy export path this intentionally does not load the bundled
placeholder mesh.  The imported WFT mesh, armature, skin weights and action
are kept, then a neutral front camera and lights are added for immediate use.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import bpy
from mathutils import Vector


def bounds(objects):
    points = []
    for obj in objects:
        if obj.type != "MESH":
            continue
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        return Vector((0, 0, 1)), 1.0
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center = (lo + hi) * 0.5
    radius = max((hi - lo).length * 0.5, 0.5)
    return center, radius


def look_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    # Blender keeps its own command-line arguments in sys.argv; application
    # arguments are conventionally placed after a standalone `--`.
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = p.parse_args(argv)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input))
    imported = list(bpy.context.scene.objects)
    meshes = [o for o in imported if o.type == "MESH"]
    armatures = [o for o in imported if o.type == "ARMATURE"]
    center, radius = bounds(imported)

    scene = bpy.context.scene
    # Blender 5.2 may expose the engine under the legacy enum in bundled
    # builds; the scene does not depend on a particular renderer.
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 960
    scene.render.resolution_y = 540
    scene.render.resolution_percentage = 100
    scene.render.fps = 30
    actions = []
    for arm in armatures:
        if arm.animation_data and arm.animation_data.action:
            actions.append(arm.animation_data.action)
    if actions:
        action = actions[0]
        start, end = int(action.frame_range[0]), int(action.frame_range[1])
        scene.frame_start, scene.frame_end = start, max(start + 1, end)
        scene.frame_set(start)
    else:
        scene.frame_start, scene.frame_end = 0, 120

    cam_data = bpy.data.cameras.new("RDR1_Front_Camera")
    cam = bpy.data.objects.new("RDR1_Front_Camera", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (center.x, center.y - radius * 3.0, center.z + radius * 0.12)
    cam_data.lens = 58
    cam_data.clip_start = 0.01
    cam_data.clip_end = max(1000.0, radius * 20.0)
    look_at(cam, center)
    scene.camera = cam

    for name, loc, energy, size in (
        ("RDR1_Key", (center.x - radius * 2, center.y - radius * 2, center.z + radius * 2), 1200, radius),
        ("RDR1_Fill", (center.x + radius * 2, center.y - radius, center.z + radius), 700, radius),
    ):
        data = bpy.data.lights.new(name, "AREA")
        data.energy, data.shape, data.size = energy, "DISK", size
        light = bpy.data.objects.new(name, data)
        scene.collection.objects.link(light)
        light.location = loc
        look_at(light, center)

    scene["rdr1_export_source"] = str(args.input)
    scene["rdr1_wft_mesh_replacement"] = bool(meshes)
    scene["rdr1_armature_count"] = len(armatures)
    scene["rdr1_action_count"] = len(actions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output))
    print(f"Saved {args.output} meshes={len(meshes)} armatures={len(armatures)} actions={len(actions)}")


if __name__ == "__main__":
    main()
