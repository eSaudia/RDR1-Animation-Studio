#!/usr/bin/env python3
"""Prepare one exported RDR1 animation blend for a simple camera render."""

from __future__ import annotations

import argparse
import math
import sys

import bpy
from mathutils import Vector


def options() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    marker = sys.argv.index("--") + 1 if "--" in sys.argv else len(sys.argv)
    return parser.parse_args(sys.argv[marker:])


def bounds(objects: list[bpy.types.Object]) -> tuple[Vector, float]:
    points = []
    for obj in objects:
        if obj.type != "MESH":
            continue
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        return Vector((0, 0, 1)), 2.0
    low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center = (low + high) * 0.5
    return center, max((high - low).length * 0.5, 1.0)


def main() -> int:
    args = options()
    bpy.ops.wm.open_mainfile(filepath=args.input)
    scene = bpy.context.scene
    meshes = [obj for obj in scene.objects if obj.type == "MESH"]
    rigs = [obj for obj in scene.objects if obj.type == "ARMATURE"]
    rig = rigs[0] if rigs else None
    action = rig.animation_data.action if rig and rig.animation_data else None
    if action:
        scene.frame_start = max(0, math.floor(action.frame_range[0]))
        scene.frame_end = max(scene.frame_start + 1, math.ceil(action.frame_range[1]))
        scene.timeline_markers.new("RDR1 clip · " + action.name, frame=scene.frame_start)
    scene.render.fps = scene.render.fps or 30
    scene.render.fps_base = 1
    scene.world.color = (0.025, 0.035, 0.05)
    for obj in scene.objects:
        obj.hide_viewport = False
        obj.hide_render = False
    center, radius = bounds(meshes)
    bpy.ops.mesh.primitive_plane_add(size=max(20.0, radius * 8.0), location=(center.x, center.y, max(0.0, center.z - radius * 0.55)))
    ground = bpy.context.object
    ground.name = "RDR1 render stage · placeholder"
    ground["rdr1_scene_asset_status"] = "placeholder"
    camera_location = center + Vector((radius * 2.6, -radius * 3.2, radius * 1.35))
    bpy.ops.object.camera_add(location=camera_location)
    camera = bpy.context.object
    camera.name = "RDR1 clip camera"
    camera.data.lens = 52
    camera.data.clip_end = max(100.0, radius * 30.0)
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    for name, location, energy, size in (
        ("Key light", center + Vector((radius * 3, -radius * 3, radius * 5)), 1200, radius * 2),
        ("Fill light", center + Vector((-radius * 3, -radius, radius * 3)), 700, radius * 2),
    ):
        data = bpy.data.lights.new(name, "AREA")
        data.energy, data.shape, data.size = energy, "DISK", size
        light = bpy.data.objects.new(name, data)
        scene.collection.objects.link(light)
        light.location = location
        light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
    scene.frame_set(scene.frame_start)
    scene["rdr1_scene_manifest"] = "{\"format\":\"rdr1-animation-studio-clip-render-v1\",\"status\":\"selected supported animation\"}"
    bpy.ops.wm.save_as_mainfile(filepath=args.output)
    print(f"Prepared clip scene: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
