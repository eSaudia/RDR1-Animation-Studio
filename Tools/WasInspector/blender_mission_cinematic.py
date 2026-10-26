#!/usr/bin/env python3
"""Build a renderable, evidence-backed RDR1 mission cinematic.

The actor motion comes from the decoded DLC_Find_Blunderbuss WAS clip. The
camera blocking and environment are explicit presentation data because this
mission's runtime camera payload is not yet decoded.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import bpy
from mathutils import Vector


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-blend", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--sfx", type=Path, default=None)
    marker = __import__("sys").argv.index("--") + 1 if "--" in __import__("sys").argv else len(__import__("sys").argv)
    return parser.parse_args(__import__("sys").argv[marker:])


def mat(name: str, color: tuple[float, float, float, float], metallic=0.0, rough=0.6):
    material = bpy.data.materials.new(name)
    material.diffuse_color = color
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = rough
        bsdf.inputs["Metallic"].default_value = metallic
    return material


def cube(name, location, scale, material, bevel=0.08):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        modifier = obj.modifiers.new("worn edges", "BEVEL")
        modifier.width = bevel
        modifier.segments = 3
    obj.data.materials.append(material)
    return obj


def clone_actor(source_objects, source_armature, scene, label, location, action_name, material=None):
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
            if material:
                for slot in clone.material_slots:
                    slot.material = material
    armature = mapping[source_armature]
    armature.hide_render = False
    armature.animation_data_create()
    source_action = bpy.data.actions.get(action_name)
    if source_action is None:
        raise RuntimeError(f"Missing decoded action {action_name}")
    action = source_action.copy()
    action.name = label + " · " + action_name
    action.use_fake_user = True
    armature.animation_data.action = action
    armature.animation_data.action_slot = action.slots[0] if action.slots else None
    armature.location = location
    armature["rdr1_scene_actor"] = label
    armature["rdr1_motion_source"] = "DLC_Find_Blunderbuss / gent_nor_hnd_knl_bbuss"
    return armature


def look_at(camera, target):
    camera.rotation_euler = (Vector(target) - camera.location).to_track_quat("-Z", "Y").to_euler()


def main() -> int:
    config = args()
    bpy.ops.wm.open_mainfile(filepath=str(config.base_blend.resolve()))
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.fps = 30
    scene.render.fps_base = 1
    scene.frame_start, scene.frame_end = 1, 360  # 12 seconds
    scene.world.color = (0.012, 0.018, 0.03)
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1280, 720, 75
    scene.render.image_settings.media_type = "VIDEO"
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.audio_codec = "AAC"
    scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
    scene.render.filepath = str(config.output.with_suffix(".mp4").resolve())

    source_objects = list(scene.objects)
    source_armature = next((obj for obj in source_objects if obj.type == "ARMATURE"), None)
    if source_armature is None:
        raise RuntimeError("The decoded mission blend has no armature")
    decoded_action = "gent_nor_hnd_knl_bbuss"
    idle_action = "gent_hostage_walk_pose"

    wood = mat("Blackwater timber", (0.20, 0.075, 0.025, 1.0), rough=0.9)
    metal = mat("Blunderbuss metal", (0.07, 0.08, 0.10, 1.0), metallic=0.8, rough=0.28)
    dirt = mat("Dry ground", (0.08, 0.055, 0.035, 1.0), rough=1.0)
    warm = mat("Warm lantern", (0.75, 0.24, 0.045, 1.0), rough=0.35)
    npc_material = mat("Blacksmith shirt", (0.14, 0.18, 0.23, 1.0), rough=0.82)

    john = clone_actor(source_objects, source_armature, scene, "John · DLC Find Blunderbuss", (-0.75, 0.15, 0.0), decoded_action)
    blacksmith = clone_actor(source_objects, source_armature, scene, "Blacksmith · interaction NPC", (1.15, 0.55, 0.0), idle_action, npc_material)
    # The source objects are no longer needed after cloning.
    for obj in source_objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    # Separate the idle NPC from the action start so the interaction reads as
    # a staged exchange, while preserving the decoded John motion exactly.
    blacksmith.rotation_euler[2] = 3.14159

    # A compact blacksmith work area: actual scene geometry rather than a
    # floating placeholder plane.
    cube("Forge table", (0.35, 1.4, 0.75), (1.55, 0.65, 0.75), wood)
    cube("Forge top", (0.35, 1.4, 1.55), (1.7, 0.8, 0.10), metal, 0.04)
    cube("Blunderbuss prop", (-0.15, 0.88, 1.72), (0.65, 0.07, 0.07), metal, 0.02)
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.16, depth=0.08, location=(0.42, 0.88, 1.72), rotation=(0, 1.5708, 0))
    bpy.context.object.name = "Blunderbuss muzzle"
    bpy.context.object.data.materials.append(metal)
    cube("Back wall", (0.0, 3.2, 2.0), (4.0, 0.08, 2.0), wood, 0.03)
    cube("Ground", (0.0, 1.2, -0.08), (5.0, 4.0, 0.08), dirt, 0.02)
    for x in (-2.8, 2.8):
        cube("Fence post", (x, 2.5, 1.2), (0.18, 0.18, 1.2), wood)

    # Lanterns and soft area lighting.
    for location in ((-2.1, 1.0, 2.7), (2.3, 2.4, 2.3)):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=20, ring_count=12, radius=0.12, location=location)
        lamp = bpy.context.object
        lamp.name = "Lantern prop"
        lamp.data.materials.append(warm)
        data = bpy.data.lights.new(lamp.name + " light", "POINT")
        data.energy, data.color = 360, (1.0, 0.20, 0.06)
        light = bpy.data.objects.new(lamp.name + " light", data)
        scene.collection.objects.link(light)
        light.location = location
    for name, location, energy, color, size in (
        ("Moon fill", (-4, -5, 6), 900, (0.18, 0.28, 1.0), 5.0),
        ("Forge key", (1, -1, 5), 1100, (1.0, 0.35, 0.08), 3.0),
    ):
        data = bpy.data.lights.new(name, "AREA")
        data.energy, data.color, data.shape, data.size = energy, color, "DISK", size
        light = bpy.data.objects.new(name, data)
        scene.collection.objects.link(light)
        light.location = location
        look_at(light, (0.2, 1.2, 1.0))

    bpy.ops.object.camera_add(location=(5.2, -6.4, 3.1))
    camera = bpy.context.object
    camera.name = "DLC Find Blunderbuss · camera"
    camera.data.lens = 48
    scene.camera = camera
    # Four readable shots: establish, action close-up, over-shoulder, resolve.
    shots = [
        (1, (6.5, -9.0, 4.0), (0.0, 0.8, 1.0), 52, "SHOT 01 · Blacksmith workshop"),
        (91, (4.0, -5.0, 2.8), (-0.2, 0.5, 1.0), 56, "SHOT 02 · John reaches for the weapon"),
        (181, (-4.0, -4.0, 2.8), (0.5, 0.7, 1.0), 52, "SHOT 03 · Blacksmith watches"),
        (271, (5.0, -7.0, 3.2), (0.0, 0.8, 1.0), 55, "SHOT 04 · Interaction resolves"),
    ]
    for frame, location, target, lens, label in shots:
        camera.location = location
        camera.data.lens = lens
        look_at(camera, target)
        camera.keyframe_insert("location", frame=frame)
        camera.keyframe_insert("rotation_euler", frame=frame)
        camera.data.keyframe_insert("lens", frame=frame)
        scene.timeline_markers.new(label, frame=frame)

    # Add the mission audio as real scene sound strips. The preferred input is
    # a byte-preserved RIFF/WAVE member extracted from the game's AWC bank.
    if config.audio.exists():
        sequence = scene.sequence_editor_create()
        sound = sequence.strips.new_sound("RDR1 original audio · AWC member", str(config.audio.resolve()), channel=1, frame_start=1)
        sound.frame_final_duration = scene.frame_end
        sound["rdr1_audio_source"] = "byte-preserved member extracted from audio_d11generic.rpf"
        sound["rdr1_audio_codec"] = "Microsoft ADPCM WAVE (format tag 2)"
        if config.sfx and config.sfx.exists():
            # The embedded WAVE member is extracted from the game's ADAT/AWC
            # bank and is retained as a real mission-relevant sound effect.
            cue = sequence.strips.new_sound("RDR1 blunderbuss SFX · extracted AWC", str(config.sfx.resolve()), channel=2, frame_start=205)
            cue.frame_final_duration = min(cue.frame_final_duration, scene.frame_end - 204)
    manifest = {
        "format": "rdr1-animation-studio-mission-cinematic-v1",
        "mission": "DLC_Find_Blunderbuss",
        "missionSource": "content.rpf/root/content/release64/dlc/zombiepack/gringos/scripts/dlc_find_blunderbuss.wsc",
        "interactionDefinition": "content.rpf/root/content/dlc/zombiepack/gringos/dlc_find_blunderbuss.xml",
        "animation": {"name": decoded_action, "frames": 186, "fps": 30, "durationSeconds": 6.166667, "decodedTracks": 68},
        "interaction": {"useTimeSeconds": 45.0, "startingPhaseTimeoutSeconds": 10.0, "capability": "Blacksmith", "playerUsable": True},
        "actors": ["John · DLC Find Blunderbuss", "Blacksmith · interaction NPC"],
        "camera": {"shots": [{"frame": frame, "label": label} for frame, _, _, _, label in shots], "status": "explicit presentation blocking; mission camera transform payload not yet decoded"},
        "world": {"status": "presentation proxy geometry", "originalWorld": False, "unresolved": ["streamed map placement", "collision/terrain", "mission prop transforms"]},
        "audio": {"path": str(config.audio.resolve()), "status": "original game audio extracted from AWC", "codec": "Microsoft ADPCM WAVE (format tag 2)", "extractedSfx": str(config.sfx.resolve()) if config.sfx else None, "sfxStatus": "byte-preserved RIFF/WAVE member extracted from dlc3_zombie/blunderbuss.awc" if config.sfx else "not included"},
    }
    scene["rdr1_scene_manifest"] = json.dumps(manifest, separators=(",", ":"))
    scene["rdr1_scene_evidence"] = "Decoded DLC_Find_Blunderbuss WAS + XML interaction definition; original AWC audio is embedded, while camera transforms and streamed world placement remain explicitly unresolved."
    config.output.parent.mkdir(parents=True, exist_ok=True)
    config.output.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    bpy.ops.wm.save_as_mainfile(filepath=str(config.output.resolve()))
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
