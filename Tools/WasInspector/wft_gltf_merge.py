#!/usr/bin/env python3
"""Replace a WAS glTF helper mesh with decoded WFT skinned character geometry."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any

from was_gltf_export import BufferBuilder


def texture_lookup(directories: list[Path]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.rglob("*.png"):
            result.setdefault(path.stem.lower(), path)
    return result


def merge(document: dict[str, Any], geometry_document: dict[str, Any],
          texture_directories: list[Path] | None = None, *, standard_normal_maps: bool = False,
          vertex_colors: bool = False) -> dict[str, Any]:
    uri = document["buffers"][0]["uri"]
    builder = BufferBuilder(document)
    builder.data = bytearray(base64.b64decode(uri.split(",", 1)[1]))
    primitives = []

    available = texture_lookup(texture_directories or [])
    document["materials"] = []
    document["images"] = []
    document["textures"] = []
    document["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]
    image_indices: dict[str, int] = {}
    material_indices: dict[tuple[str, ...], int] = {}

    def add_texture(name: str) -> int | None:
        key = Path(name).stem.lower()
        path = available.get(key)
        if path is None:
            return None
        if key not in image_indices:
            image_index = len(document["images"])
            document["images"].append({
                "name": path.stem,
                "uri": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii"),
            })
            image_indices[key] = image_index
            document["textures"].append({"name": path.stem, "sampler": 0, "source": image_index})
        return image_indices[key]

    def add_material(names: list[str]) -> int:
        key = tuple(name.lower() for name in names)
        if key in material_indices:
            return material_indices[key]
        diffuse = add_texture(names[0]) if names else None
        # RDR1 commonly stores normals as packed ATI2 data. The current PNG conversion
        # does not reconstruct standard RGB tangent normals, so linking it directly produces
        # black, faceted Blender shading. Keep the source name in extras and make this opt-in.
        normal = add_texture(names[2]) if standard_normal_maps and len(names) > 2 else None
        material: dict[str, Any] = {
            "name": Path(names[0]).stem if names else "RDR1 character preview",
            "doubleSided": True,
            "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0,
                                     "roughnessFactor": 0.8},
            "extras": {"sourceTextures": names},
        }
        if diffuse is not None:
            material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": diffuse}
        if normal is not None:
            material["normalTexture"] = {"index": normal, "scale": 0.5}
        if any("alphakill" in name.lower() or "alphacrop" in name.lower() for name in names[:1]):
            material["alphaMode"] = "MASK"
            material["alphaCutoff"] = 0.5
        index = len(document["materials"])
        document["materials"].append(material)
        material_indices[key] = index
        return index

    for geometry in geometry_document["geometries"]:
        vertex_count = geometry["vertex_count"]
        attributes = {
            "POSITION": builder.add((v for row in geometry["positions"] for v in row), 5126, "VEC3",
                                    vertex_count, target=34962, bounds=True),
            "JOINTS_0": builder.add((v for row in geometry["blend_indices"] for v in row), 5123, "VEC4",
                                    vertex_count, target=34962),
            "WEIGHTS_0": builder.add((v for row in geometry["blend_weights"] for v in row), 5126, "VEC4",
                                     vertex_count, target=34962),
        }
        if len(geometry["normals"]) == vertex_count:
            attributes["NORMAL"] = builder.add((v for row in geometry["normals"] for v in row), 5126, "VEC3",
                                               vertex_count, target=34962)
        if len(geometry["texcoords"]) == vertex_count:
            # glTF uses an upper-left UV origin relative to the decoded game convention.
            attributes["TEXCOORD_0"] = builder.add(
                (v for uv in geometry["texcoords"] for v in (uv[0], 1.0-uv[1])), 5126, "VEC2",
                vertex_count, target=34962)
        if vertex_colors and len(geometry.get("colors", [])) == vertex_count:
            attributes["COLOR_0"] = builder.add(
                # RDR uses the fourth channel for shader-specific data, not universal opacity.
                # Exporting it as glTF color alpha makes alpha-masked faces and clothing vanish.
                (v for row in geometry["colors"] for v in row[:3]), 5126, "VEC3", vertex_count, target=34962)
        if len(geometry.get("tangents", [])) == vertex_count:
            attributes["TANGENT"] = builder.add(
                (v for row in geometry["tangents"] for v in row), 5126, "VEC4", vertex_count, target=34962)
        indices = builder.add(geometry["indices"], 5123, "SCALAR", len(geometry["indices"]), target=34963)
        shader_names = geometry.get("shader_textures", [])
        material = add_material(shader_names)
        primitives.append({"attributes": attributes, "indices": indices, "material": material, "mode": 4,
                           "extras": {"sourceGeometry": geometry["index"],
                                      "sourceShader": geometry.get("shader_id"),
                                      "sourceTextures": shader_names}})

    document["meshes"][0] = {"name": "player_default", "primitives": primitives}
    helper_node = next(node for node in document["nodes"] if node.get("mesh") == 0)
    helper_node["name"] = "player_default"
    document["buffers"][0] = {
        "byteLength": len(builder.data),
        "uri": "data:application/octet-stream;base64," + base64.b64encode(builder.data).decode("ascii"),
    }
    document.setdefault("extras", {})["wftGeometryCount"] = len(primitives)
    document["extras"]["wftVertexCount"] = sum(g["vertex_count"] for g in geometry_document["geometries"])
    document["extras"]["wftFaceCount"] = sum(g["face_count"] for g in geometry_document["geometries"])
    document["extras"]["wftMaterialCount"] = len(document["materials"])
    document["extras"]["wftEmbeddedTextureCount"] = len(document["images"])
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("animation_gltf", type=Path)
    parser.add_argument("geometry_json", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--texture-dir", action="append", type=Path, default=[],
                        help="Directory containing PNG textures exported from matching WTD dictionaries (repeatable).")
    parser.add_argument("--standard-normal-maps", action="store_true",
                        help="Link exported normal PNGs (only use when they have been reconstructed as RGB normals).")
    parser.add_argument("--vertex-colors", action="store_true",
                        help="Multiply materials by RDR vertex RGB (diagnostic; semantics vary by shader).")
    args = parser.parse_args()
    document = json.loads(args.animation_gltf.read_text(encoding="utf-8"))
    geometry = json.loads(args.geometry_json.read_text(encoding="utf-8"))
    result = merge(document, geometry, args.texture_dir,
                   standard_normal_maps=args.standard_normal_maps, vertex_colors=args.vertex_colors)
    args.output.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Exported {result['extras']['wftVertexCount']} vertices, {result['extras']['wftFaceCount']} faces, "
          f"{result['extras']['wftMaterialCount']} materials, and "
          f"{result['extras']['wftEmbeddedTextureCount']} embedded textures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
