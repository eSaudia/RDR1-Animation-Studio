"""Build a Blender scene from validated WSI placement and WVD mesh evidence.

The scene is intentionally evidence-first: only WVD geometry with decoded
positions/indices and WSI instances with validated transforms are placed.
Materials, terrain, room streaming, and mission ownership are not guessed.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import bpy
from mathutils import Matrix, Vector


def options() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-instances", type=int, default=1000)
    marker = sys.argv.index("--") + 1 if "--" in sys.argv else len(sys.argv)
    return parser.parse_args(sys.argv[marker:])


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def parse_vector(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, list) and len(value) >= 3:
        try:
            result = tuple(float(value[index]) for index in range(3))
            return result if all(math.isfinite(number) for number in result) else None
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        parts = value.strip().split()
        if len(parts) >= 3:
            try:
                result = tuple(float(parts[index]) for index in range(3))
                return result if all(math.isfinite(number) for number in result) else None
            except (TypeError, ValueError):
                return None
    return None


def parse_indices(value: Any) -> list[int]:
    if isinstance(value, list):
        result = []
        for item in value:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                return []
        return result
    if isinstance(value, str):
        return [int(item) for item in re.findall(r"-?\d+", value)]
    return []


def blender_matrix(value: Any) -> Matrix | None:
    if not isinstance(value, list) or len(value) < 3:
        return None
    rows: list[list[float]] = []
    for row in value[:4]:
        if not isinstance(row, list) or len(row) < 3:
            return None
        try:
            rows.append([float(item) for item in row[:4]])
        except (TypeError, ValueError):
            return None
    if len(rows) < 4:
        translation = [0.0, 0.0, 0.0]
        if isinstance(value[3], list) and len(value[3]) >= 3:
            translation = [float(item) for item in value[3][:3]]
        rows.append([translation[0], translation[1], translation[2], 0.0])
    if not all(math.isfinite(item) for row in rows for item in row[:3]):
        return None
    # WSI stores the basis and translation as a row-vector matrix. Blender
    # uses column vectors, so transpose the 3x3 basis and put translation in
    # the final column. The decoder's M44 is padding and may be zero.
    return Matrix((
        (rows[0][0], rows[1][0], rows[2][0], rows[3][0]),
        (rows[0][1], rows[1][1], rows[2][1], rows[3][1]),
        (rows[0][2], rows[1][2], rows[2][2], rows[3][2]),
        (0.0, 0.0, 0.0, 1.0),
    ))


def make_material(name: str, hash_text: str) -> bpy.types.Material:
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.diffuse_color = (0.48, 0.50, 0.54, 1.0)
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF") if material.node_tree else None
    if shader:
        shader.inputs["Base Color"].default_value = (0.48, 0.50, 0.54, 1.0)
        shader.inputs["Roughness"].default_value = 0.88
    material["rdr1_material_status"] = "placeholder: WVD shader/material binding unresolved"
    material["rdr1_drawable_hash"] = hash_text
    return material


def geometry_records(report: dict[str, Any], drawable_index: int) -> list[tuple[int, list[tuple[float, float, float]], list[tuple[int, int, int]]]]:
    decoded = report.get("decoded", {})
    drawables = decoded.get("drawables", []) if isinstance(decoded, dict) else []
    if not isinstance(drawables, list) or drawable_index < 0 or drawable_index >= len(drawables):
        return []
    drawable = drawables[drawable_index]
    if not isinstance(drawable, dict):
        return []
    for lod in drawable.get("lods", []):
        if not isinstance(lod, dict) or lod.get("name") != "high":
            continue
        records: list[tuple[int, list[tuple[float, float, float]], list[tuple[int, int, int]]]] = []
        for model in lod.get("models", []):
            if not isinstance(model, dict):
                continue
            for geometry in model.get("geometries", []):
                if not isinstance(geometry, dict) or not geometry.get("positionDecoded"):
                    continue
                vertices = [parse_vector(item) for item in geometry.get("positions", [])]
                vertices = [item for item in vertices if item is not None]
                indices = parse_indices(geometry.get("indexBuffer", {}).get("indices"))
                if len(vertices) < 3 or len(indices) < 3:
                    continue
                faces = []
                for index in range(0, len(indices) - 2, 3):
                    triangle = indices[index:index + 3]
                    if len(triangle) == 3 and all(0 <= value < len(vertices) for value in triangle):
                        faces.append((triangle[0], triangle[1], triangle[2]))
                if faces:
                    records.append((int(geometry.get("index", len(records))), vertices, faces))
        if records:
            return records
    return []


def mesh_for_geometry(key: str, geometry_index: int, vertices, faces, material):
    name = f"WVD {key} geometry {geometry_index}"
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.validate(verbose=False)
    mesh.update(calc_edges=True)
    mesh.materials.append(material)
    mesh["rdr1_mesh_evidence"] = "decoded WVD positions and indexed triangles"
    return mesh


def main() -> int:
    config = options()
    package = config.package.resolve()
    join_path = package / "placement-join.json"
    if not join_path.is_file():
        raise FileNotFoundError(f"Missing placement join report: {join_path}")
    join = load_json(join_path)
    instances = join.get("instances", [])
    if not isinstance(instances, list):
        raise ValueError("placement join has no instance list")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.name = "RDR1 Armadillo · decoded placement evidence"
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    scene.render.resolution_percentage = 100
    scene.render.fps = 30
    scene.frame_start, scene.frame_end = 1, 240
    # Blender 5.2 no longer exposes FFMPEG as an image_settings format. Keep
    # the scene render-ready; the studio's MP4 command selects the video
    # encoder when it launches Blender for a movie render.
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(config.output.with_suffix(".png").resolve())
    scene.world = bpy.data.worlds.new("RDR1 evidence world")
    scene.world.color = (0.035, 0.045, 0.065)

    root = bpy.data.collections.new("RDR1 World Cell · Armadillo")
    scene.collection.children.link(root)
    placed_collection = bpy.data.collections.new("Placed WSI instances · exact hash matches")
    root.children.link(placed_collection)
    unresolved_collection = bpy.data.collections.new("Unmatched WSI instances · evidence only")
    root.children.link(unresolved_collection)
    cache: dict[tuple[str, int], list[Any]] = {}
    report_cache: dict[str, dict[str, Any]] = {}
    placed_locations: list[Vector] = []
    placed_count = 0
    geometry_count = 0
    skipped = 0

    for instance in instances[: max(0, config.max_instances)]:
        if not isinstance(instance, dict):
            continue
        matches = instance.get("visualMatches", [])
        matrix = blender_matrix(instance.get("matrix"))
        if not isinstance(matches, list) or not matches or matrix is None:
            skipped += 1
            continue
        match = next((item for item in matches if isinstance(item, dict)), None)
        if match is None:
            skipped += 1
            continue
        report_name = str(match.get("report", ""))
        report_path = package / Path(report_name)
        if not report_path.is_file():
            skipped += 1
            continue
        report_key = report_path.as_posix()
        report = report_cache.setdefault(report_key, load_json(report_path))
        drawable_index = int(match.get("drawableIndex", -1))
        cache_key = (report_key, drawable_index)
        if cache_key not in cache:
            key = f"{Path(report_name).stem} · {match.get('hash', 'unknown')}"
            material = make_material("RDR1 neutral evidence material · " + str(match.get("hash", "unknown")), str(match.get("hash", "unknown")))
            meshes = []
            for geometry_index, vertices, faces in geometry_records(report, drawable_index):
                meshes.append(mesh_for_geometry(key, geometry_index, vertices, faces, material))
            cache[cache_key] = meshes
        meshes = cache[cache_key]
        if not meshes:
            skipped += 1
            continue
        label = str(instance.get("name") or "unnamed")
        base_name = f"{label} · {instance.get('sectorIndex', 0)}:{instance.get('instanceIndex', 0)} · {instance.get('instanceHash', 'hash')}"
        for mesh_index, mesh in enumerate(meshes):
            obj = bpy.data.objects.new(base_name + f" · geometry {mesh_index}", mesh)
            placed_collection.objects.link(obj)
            obj.matrix_world = matrix
            obj["rdr1_instance_name"] = label
            obj["rdr1_instance_hash"] = str(instance.get("instanceHash", ""))
            obj["rdr1_source_wsi"] = str(join.get("wsiReport", ""))
            obj["rdr1_visual_match"] = report_name
            obj["rdr1_material_status"] = "placeholder: shader/material binding unresolved"
            geometry_count += 1
        # The two dictionary-root instances carry a valid visual hash but a
        # zero translation; keep their evidence objects, but do not let those
        # roots pull the preview camera away from the placed Armadillo cell.
        if matrix.translation.length > 1.0:
            placed_locations.append(matrix.translation.copy())
        placed_count += 1

    for instance in instances[: max(0, config.max_instances)]:
        if not isinstance(instance, dict) or instance.get("matchStatus") == "matched-by-hash":
            continue
        location = parse_vector(instance.get("translation"))
        if location is None:
            continue
        empty = bpy.data.objects.new(f"unmatched · {instance.get('name') or 'unnamed'} · {instance.get('instanceHash', '')}", None)
        unresolved_collection.objects.link(empty)
        empty.empty_display_type = "CUBE"
        empty.empty_display_size = 0.35
        empty.location = location
        empty["rdr1_instance_hash"] = str(instance.get("instanceHash", ""))
        empty["rdr1_status"] = "no WVD visual dictionary hash match"

    if placed_locations:
        minimum = Vector((min(item.x for item in placed_locations), min(item.y for item in placed_locations), min(item.z for item in placed_locations)))
        maximum = Vector((max(item.x for item in placed_locations), max(item.y for item in placed_locations), max(item.z for item in placed_locations)))
        center = (minimum + maximum) * 0.5
        radius = max(25.0, (maximum - minimum).length * 0.65)
    else:
        center, radius = Vector((0, 0, 0)), 25.0
    ground_material = make_material("RDR1 placement floor · unresolved terrain", "terrain")
    bpy.ops.mesh.primitive_plane_add(size=max(100.0, radius * 3.0), location=(center.x, center.y, center.z - 0.2))
    floor = bpy.context.object
    floor.name = "Evidence floor · terrain not decoded"
    floor.data.materials.append(ground_material)
    floor["rdr1_status"] = "presentation floor only; game terrain is unresolved"
    root.objects.link(floor)
    for collection in list(floor.users_collection):
        if collection != root:
            collection.objects.unlink(floor)

    target = bpy.data.objects.new("Camera target · placement center", None)
    root.objects.link(target)
    target.location = center
    target.empty_display_type = "SPHERE"
    target.empty_display_size = 0.6
    camera_data = bpy.data.cameras.new("RDR1 placement camera")
    camera = bpy.data.objects.new("RDR1 placement camera", camera_data)
    root.objects.link(camera)
    camera.location = center + Vector((radius * 1.05, -radius * 1.05, radius * 0.60 + 5.0))
    camera_data.lens = 48
    camera_data.clip_end = 100000.0
    camera.rotation_euler = (target.location - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    light_data = bpy.data.lights.new("RDR1 placement sun", "SUN")
    light_data.energy = 3.0
    sun = bpy.data.objects.new("RDR1 placement sun", light_data)
    root.objects.link(sun)
    sun.rotation_euler = (math.radians(28), math.radians(-18), math.radians(28))

    evidence = {
        "format": "rdr1-world-cell-blender-scene-v0.27",
        "source": str(join_path),
        "placementsRequested": min(len(instances), max(0, config.max_instances)),
        "placementsPlaced": placed_count,
        "meshObjects": geometry_count,
        "unmatchedMarkers": sum(1 for item in instances[: max(0, config.max_instances)] if isinstance(item, dict) and item.get("matchStatus") != "matched-by-hash"),
        "skipped": skipped,
        "fidelity": "WSI transforms and WVD indexed geometry are real decoded evidence; materials, terrain, room streaming, audio, AI, and mission ownership remain unresolved",
    }
    scene["rdr1_world_cell_evidence"] = json.dumps(evidence, separators=(",", ":"))
    scene["rdr1_world_cell_source"] = str(join_path)
    config.output.parent.mkdir(parents=True, exist_ok=True)
    config.output.with_suffix(".scene.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    bpy.ops.wm.save_as_mainfile(filepath=str(config.output.resolve()))
    print(json.dumps(evidence, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
