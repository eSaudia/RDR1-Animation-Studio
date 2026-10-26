#!/usr/bin/env python3
"""Build a readable RDR1-style street scene from the bundled character rig.

This is the safe visual preview path for the studio.  It uses the real bundled
RDR1 character geometry/materials and animation action, then adds explicit
presentation geometry and camera blocking.  It does not claim to be a decoded
world/cutscene resource: those runtime payloads remain separate evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def options() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-blend", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scene-name", default="RDR1-main-street")
    parser.add_argument("--scene-kind", default="unknown")
    parser.add_argument("--source-path", default="")
    parser.add_argument("--semantic-report", type=Path)
    marker = sys.argv.index("--") + 1 if "--" in sys.argv else len(sys.argv)
    return parser.parse_args(sys.argv[marker:])


def _number(value):
    try:
        return float(str(value).strip().strip('"'))
    except (TypeError, ValueError):
        return None


def _vector(value):
    parts = str(value).strip().strip('"').split()
    if len(parts) != 3:
        return None
    numbers = [_number(part) for part in parts]
    return tuple(numbers) if all(number is not None for number in numbers) else None


def semantic_camera_shots(report_path: Path | None):
    """Read only verified printable camera labels/values from a semantic report.

    Camera payload fields are still game-resource evidence, not a fully decoded
    runtime schema.  When a report contains the stable CutsceneCameraShotTuning
    labels, however, their position/target/FOV values are safe to use for a
    visual preview.
    """
    if report_path is None or not report_path.is_file():
        return []
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        values = [item.get("value", "") for item in report.get("strings", []) if isinstance(item, dict)]
    except (OSError, ValueError, TypeError):
        return []
    shots = []
    starts = [index for index, value in enumerate(values) if value == "CutsceneCameraShotTuning"]
    for shot_index, start in enumerate(starts):
        end = starts[shot_index + 1] if shot_index + 1 < len(starts) else len(values)
        block = values[start:end]
        positions = [index for index, value in enumerate(block) if value == "Position"]
        if not positions:
            continue
        position = next((_vector(block[index + 1]) for index in positions if index + 1 < len(block)), None)
        target = None
        for index, value in enumerate(block):
            if value == "TargetWorldSpace":
                target_positions = [candidate for candidate in positions if candidate > index]
                if target_positions:
                    candidate = target_positions[0]
                    if candidate + 1 < len(block):
                        target = _vector(block[candidate + 1])
                break
        if position is None or target is None:
            continue
        fov = None
        for index, value in enumerate(block):
            if value in {"FOVDegrees", "FocalLength"} and index + 1 < len(block):
                fov = _number(block[index + 1])
                if fov is not None:
                    break
        duration = None
        for index, value in enumerate(block):
            if value == "Duration" and index + 1 < len(block):
                duration = _number(block[index + 1])
                if duration is not None:
                    break
        name = next((str(value).strip('"') for value in block if str(value).startswith('"') and str(value).endswith('"')), f"Shot {shot_index + 1}")
        shots.append({"name": name, "position": position, "target": target, "fov": fov, "duration": duration})
    return shots


def normalize_camera_shots(raw_shots):
    if not raw_shots:
        return []
    center = Vector((0, 0, 0))
    for shot in raw_shots:
        center += Vector(shot["position"])
    center /= len(raw_shots)
    spread = max((Vector(shot["position"]) - center).length for shot in raw_shots)
    scale = min(1.0, 3.5 / max(spread, 3.5))
    result = []
    for shot in raw_shots[:24]:
        position = Vector(shot["position"])
        target = Vector(shot["target"])
        result.append({
            "name": shot["name"],
            "position": tuple(Vector((0, -8.0, 3.0)) + (position - center) * scale),
            "target": tuple(Vector((0, 1.0, 1.1)) + (target - center) * scale),
            "lens": max(28.0, min(75.0, 50.0 if shot.get("fov") is None else 50.0 * 45.0 / max(float(shot["fov"]), 10.0))),
            "duration": max(1.0, float(shot.get("duration") or 3.0)),
        })
    return result


def material(name: str, color: tuple[float, float, float, float], roughness: float = 0.75, metallic: float = 0.0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
    return mat


def cube(name, location, scale, mat, bevel=0.0):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    if bevel:
        modifier = obj.modifiers.new("worn edges", "BEVEL")
        modifier.width = bevel
        modifier.segments = 2
    return obj


def cylinder(name, location, radius, depth, mat, vertices=20):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    return obj


def look_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def clone_actor(source_objects, source_armature, scene, label, location, action_name, facing=math.pi):
    collection = bpy.data.collections.new(label + " · Actor")
    scene.collection.children.link(collection)
    mapping = {}
    for source in source_objects:
        if source.type not in {"MESH", "ARMATURE"} or source.name == "Icosphere":
            continue
        clone = source.copy()
        clone.data = source.data.copy() if source.data is not None else None
        clone.name = label + " · " + source.name
        clone.hide_render = False
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
    action = bpy.data.actions.get(action_name)
    if action is None:
        raise RuntimeError(f"The bundled rig is missing action {action_name!r}")
    action = action.copy()
    action.name = label + " · " + action_name
    action.use_fake_user = True
    armature.animation_data_create()
    # NLA repeat keeps the extracted motion alive for the whole cinematic.
    track = armature.animation_data.nla_tracks.new()
    track.name = label + " · motion"
    strip = track.strips.new(label + " · loop", 1, action)
    strip.repeat = 24.0
    armature.rotation_euler[2] = facing
    armature.location = location
    armature["rdr1_scene_actor"] = label
    armature["rdr1_motion_source"] = action_name
    return armature


def add_text(scene, text, location, size, mat, name, rotation=(math.pi / 2, 0, 0)):
    curve = bpy.data.curves.new(name + " curve", "FONT")
    curve.body = text
    curve.align_x = "CENTER"
    curve.size = size
    curve.extrude = 0.012
    obj = bpy.data.objects.new(name, curve)
    scene.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = rotation
    obj.data.materials.append(mat)
    return obj


def main() -> int:
    config = options()
    bpy.ops.wm.open_mainfile(filepath=str(config.base_blend.resolve()))
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.fps = 30
    scene.render.fps_base = 1
    scene.frame_start, scene.frame_end = 1, 720
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1280, 720, 100
    scene.world.color = (0.36, 0.53, 0.72)
    scene.render.image_settings.media_type = "VIDEO"
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
    scene.render.filepath = str(config.output.with_suffix(".mp4").resolve())

    source_objects = list(scene.objects)
    source_armature = next((obj for obj in source_objects if obj.type == "ARMATURE"), None)
    if source_armature is None:
        raise RuntimeError("The bundled character scene has no armature.")
    actions = [a.name for a in bpy.data.actions]
    walk = "gent_hostage_walk_pose" if "gent_hostage_walk_pose" in actions else actions[0]

    brick = material("sun-baked brick", (0.26, 0.07, 0.035, 1), roughness=0.95)
    wood = material("weathered timber", (0.20, 0.075, 0.028, 1), roughness=0.88)
    wood_light = material("sunlit timber", (0.40, 0.16, 0.055, 1), roughness=0.86)
    road = material("dusty Main Street", (0.20, 0.14, 0.085, 1), roughness=1.0)
    roof = material("dark roof", (0.035, 0.045, 0.055, 1), roughness=0.9)
    glass = material("old glass", (0.08, 0.20, 0.27, 1), roughness=0.25, metallic=0.1)
    brass = material("sign brass", (0.72, 0.36, 0.06, 1), roughness=0.35, metallic=0.25)
    paper = material("aged sign", (0.73, 0.58, 0.34, 1), roughness=0.82)

    # Actors use the real packed RDR1 rig.  The rig's bind bounds extend below
    # its origin, so z=0.95 is intentional: it places both feet on the street.
    actors = []
    actor_specs = [
        # The bundled rig faces +Y in bind space.  The camera starts at -Y,
        # so turn the cast toward lens for the readable walk-in shot.
        ("John Marston", (-1.55, 0.05, 0.95), math.pi),
        ("Deputy", (0.0, -0.20, 0.95), math.pi),
        ("Ranch hand", (1.55, 0.15, 0.95), math.pi),
    ]
    for label, location, facing in actor_specs:
        actor = clone_actor(source_objects, source_armature, scene, label, location, walk, facing)
        root = bpy.data.objects.new(label + " · walk path", None)
        scene.collection.objects.link(root)
        world = actor.matrix_world.copy()
        actor.parent = root
        actor.matrix_world = world
        root.location = location
        root.keyframe_insert("location", frame=1)
        root.location.y = 4.2 + (0.25 if label == "Ranch hand" else 0.0)
        root.keyframe_insert("location", frame=720)
        actors.append(label)
    for obj in source_objects:
        bpy.data.objects.remove(obj, do_unlink=True)

    # Main Street set: facades, storefronts, windows, awnings, a raised boardwalk,
    # telegraph poles and a readable town sign.  These are presentation geometry,
    # not guessed replacements for an undecoded WFT/WPDT world cell.
    cube("street", (0, 2.5, -0.05), (12, 12, 0.05), road, 0.02)
    for side in (-1, 1):
        x = side * 5.0
        cube("brick building", (x, 3.6, 2.6), (1.55, 5.7, 2.6), brick, 0.06)
        cube("timber storefront", (side * 3.35, 0.8, 1.75), (0.14, 2.6, 1.75), wood, 0.03)
        for y in (-0.8, 1.3, 3.4):
            cube("window", (side * 3.16, y, 2.55), (0.035, 0.62, 0.55), glass, 0.02)
            cube("window frame", (side * 3.10, y, 2.55), (0.05, 0.72, 0.68), wood_light, 0.01)
        cube("awning", (side * 3.15, 0.8, 1.25), (0.50, 2.25, 0.08), paper, 0.02)
    cube("far street facade", (0, 8.8, 2.2), (6.0, 0.20, 2.2), brick, 0.04)
    for x in (-3.7, -1.2, 1.2, 3.7):
        cube("far window", (x, 8.54, 2.7), (0.75, 0.04, 0.70), glass, 0.02)
    cube("left boardwalk", (-3.35, 2.4, 0.16), (0.45, 5.2, 0.16), wood_light, 0.02)
    cube("right boardwalk", (3.35, 2.4, 0.16), (0.45, 5.2, 0.16), wood_light, 0.02)

    for x in (-4.1, 4.1):
        cylinder("telegraph pole", (x, 3.5, 3.5), 0.11, 7.0, wood, 16)
        cube("telegraph crossbar", (x, 3.5, 6.2), (0.75, 0.10, 0.08), wood, 0.02)
        for wire_x in (-0.55, 0.55):
            bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.018, depth=12.0, location=(x + wire_x, 3.5, 6.35), rotation=(0, math.pi / 2, 0))
            bpy.context.object.name = "telegraph wire"
            bpy.context.object.data.materials.append(roof)

    cube("Main St sign board", (0.0, 5.9, 3.9), (1.35, 0.08, 0.34), paper, 0.03)
    add_text(scene, "MAIN ST.", (0.0, 5.79, 3.87), 0.30, brass, "Main St. sign")
    resource_label = config.scene_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    add_text(scene, resource_label[:24].upper(), (0.0, 5.70, 3.42), 0.15, brass, "Selected resource sign")
    add_text(scene, "GENERAL STORE", (-3.35, -1.84, 2.0), 0.22, brass, "store sign")

    # Warm sun and cool sky fill create the cinematic contrast visible in the
    # reference shot while keeping the real rig readable.
    sun_data = bpy.data.lights.new("late afternoon sun", "AREA")
    sun_data.energy, sun_data.color, sun_data.shape, sun_data.size = 1000, (1.0, 0.72, 0.50), "DISK", 5.0
    sun = bpy.data.objects.new("late afternoon sun", sun_data)
    scene.collection.objects.link(sun); sun.location = (-4, -6, 8); look_at(sun, (0, 2, 1))
    fill_data = bpy.data.lights.new("blue sky fill", "AREA")
    fill_data.energy, fill_data.color, fill_data.shape, fill_data.size = 1700, (0.42, 0.62, 1.0), "DISK", 7.0
    fill = bpy.data.objects.new("blue sky fill", fill_data)
    scene.collection.objects.link(fill); fill.location = (5, 4, 6); look_at(fill, (0, 2, 1))
    # A soft camera-side fill keeps the packed character materials readable
    # without flattening the warm/cool street contrast.
    front_data = bpy.data.lights.new("camera softbox", "AREA")
    front_data.energy, front_data.color, front_data.shape, front_data.size = 1250, (1.0, 0.92, 0.82), "DISK", 4.0
    front = bpy.data.objects.new("camera softbox", front_data)
    scene.collection.objects.link(front); front.location = (0, -5, 4); look_at(front, (0, 1.5, 1.0))

    bpy.ops.object.camera_add(location=(7.2, -10.5, 3.2))
    camera = bpy.context.object
    camera.name = "Selected resource camera"
    scene.camera = camera
    parsed_shots = normalize_camera_shots(semantic_camera_shots(config.semantic_report))
    if parsed_shots:
        total_seconds = sum(shot["duration"] for shot in parsed_shots)
        scene.frame_end = max(60, int(total_seconds * scene.render.fps))
        shots = []
        cursor = 1
        for shot in parsed_shots:
            shots.append((cursor, shot["position"], shot["target"], shot["lens"], shot["name"]))
            cursor += max(1, int(shot["duration"] * scene.render.fps))
    else:
        shots = [
            (1, (7.2, -10.5, 3.2), (0, 1.7, 1.05), 52, "SHOT 01 · fallback establish"),
            (181, (4.2, -7.2, 2.5), (0, 1.2, 1.05), 58, "SHOT 02 · fallback approach"),
            (361, (-4.6, -5.2, 2.35), (0, 2.1, 1.1), 56, "SHOT 03 · fallback cutaway"),
            (541, (0.4, -8.0, 2.45), (0, 2.7, 1.1), 60, "SHOT 04 · fallback resolve"),
        ]
    for frame, location, target, lens, label in shots:
        camera.location = location; camera.data.lens = lens; look_at(camera, target)
        camera.keyframe_insert("location", frame=frame)
        camera.keyframe_insert("rotation_euler", frame=frame)
        camera.data.keyframe_insert("lens", frame=frame)
        scene.timeline_markers.new(label, frame=frame)

    manifest = {
        "format": "rdr1-animation-studio-resource-scene-v2",
        "scene": config.scene_name,
        "resourceKind": config.scene_kind,
        "sourcePath": config.source_path,
        "fps": int(scene.render.fps),
        "frames": int(scene.frame_end),
        "actors": actors,
        "motion": {"action": walk, "source": "bundled RDR1 rig", "looped": True},
        "cameraShots": [{"frame": f, "name": n} for f, _, _, _, n in shots],
        "cameraSource": "verified semantic camera strings" if parsed_shots else "presentation fallback (no camera shot records decoded)",
        "fidelity": "Real bundled RDR1 character geometry/materials and animation; selected resource drives scene identity and decoded camera shots when available. Runtime world streaming, props, AI, and audio payloads remain unresolved.",
    }
    scene["rdr1_scene_manifest"] = json.dumps(manifest, separators=(",", ":"))
    scene["rdr1_scene_evidence"] = manifest["fidelity"]
    config.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(config.output.resolve()))
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
