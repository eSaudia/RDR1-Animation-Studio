#!/usr/bin/env python3
"""Export a skeleton-bound RDR1 WAS animation as an embedded glTF 2.0 file."""

from __future__ import annotations

import argparse
import base64
import json
import math
import struct
from pathlib import Path
from typing import Any, Iterable


def quat_matrix(q: list[float]) -> list[list[float]]:
    x, y, z, w = q
    n = math.sqrt(x*x + y*y + z*z + w*w) or 1.0
    x, y, z, w = x/n, y/n, z/n, w/n
    return [
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w), 0],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w), 0],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y), 0],
        [0, 0, 0, 1],
    ]


def local_matrix(position: list[float], rotation: list[float]) -> list[list[float]]:
    matrix = quat_matrix(rotation)
    matrix[0][3], matrix[1][3], matrix[2][3] = position
    return matrix


def mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def inverse_rigid(matrix: list[list[float]]) -> list[list[float]]:
    result = [[0.0] * 4 for _ in range(4)]
    for r in range(3):
        for c in range(3):
            result[r][c] = matrix[c][r]
    for r in range(3):
        result[r][3] = -sum(result[r][c] * matrix[c][3] for c in range(3))
    result[3][3] = 1.0
    return result


def column_major(matrix: list[list[float]]) -> list[float]:
    return [matrix[r][c] for c in range(4) for r in range(4)]


class BufferBuilder:
    def __init__(self, document: dict[str, Any]):
        self.data = bytearray()
        self.document = document

    def add(self, values: Iterable[float | int], component_type: int, kind: str,
            count: int, *, target: int | None = None, bounds: bool = False) -> int:
        formats = {5126: "f", 5123: "H", 5121: "B"}
        values = list(values)
        while len(self.data) % 4:
            self.data.append(0)
        offset = len(self.data)
        self.data.extend(struct.pack("<" + formats[component_type] * len(values), *values))
        view: dict[str, Any] = {"buffer": 0, "byteOffset": offset, "byteLength": len(self.data)-offset}
        if target is not None:
            view["target"] = target
        view_index = len(self.document["bufferViews"])
        self.document["bufferViews"].append(view)
        accessor: dict[str, Any] = {
            "bufferView": view_index, "componentType": component_type, "count": count, "type": kind,
        }
        if bounds and values:
            width = {"SCALAR": 1, "VEC3": 3}[kind]
            rows = [values[i:i+width] for i in range(0, len(values), width)]
            accessor["min"] = [min(row[i] for row in rows) for i in range(width)]
            accessor["max"] = [max(row[i] for row in rows) for i in range(width)]
        accessor_index = len(self.document["accessors"])
        self.document["accessors"].append(accessor)
        return accessor_index


def fit_samples(samples: list[list[float]], frame_count: int) -> list[list[float]]:
    """Fit subordinate sequence samples to the animation's master timeline."""
    if len(samples) == frame_count:
        return samples
    if not samples:
        raise ValueError("Animation track has no samples.")
    if len(samples) == 1:
        return samples * frame_count
    if frame_count == 1:
        return [samples[0]]
    return [samples[round(frame * (len(samples) - 1) / (frame_count - 1))]
            for frame in range(frame_count)]


def export_gltf(source: dict[str, Any], *, root_tracks: bool = True) -> dict[str, Any]:
    skeleton = source["skeleton"]
    bones = sorted(skeleton["bones"], key=lambda bone: bone["index"])
    document: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "RDR1 Animation Studio"},
        "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "skins": [],
        "meshes": [], "materials": [], "animations": [], "accessors": [], "bufferViews": [], "buffers": [],
        "extras": {"sourceAnimation": source["animation"]["name"], "sourceModel": skeleton.get("model")},
    }
    builder = BufferBuilder(document)

    for bone in bones:
        node: dict[str, Any] = {
            "name": bone["name"], "translation": bone["position"], "rotation": bone["rotation_xyzw"],
        }
        children = [candidate["index"] for candidate in bones if candidate["parent_index"] == bone["index"]]
        if children:
            node["children"] = children
        document["nodes"].append(node)
    roots = [bone["index"] for bone in bones if bone["parent_index"] < 0]
    document["scenes"][0]["nodes"].extend(roots)

    globals_: list[list[list[float]]] = []
    for bone in bones:
        local = local_matrix(bone["position"], bone["rotation_xyzw"])
        parent = bone["parent_index"]
        globals_.append(local if parent < 0 else mat_mul(globals_[parent], local))
    inverse_accessor = builder.add(
        (value for matrix in globals_ for value in column_major(inverse_rigid(matrix))), 5126, "MAT4", len(bones)
    )
    document["skins"].append({"name": skeleton.get("model") or "RDR1 Skeleton", "joints": list(range(len(bones))),
                              "skeleton": roots[0], "inverseBindMatrices": inverse_accessor})

    # A transparent, root-weighted triangle makes importers construct an armature/skin.
    position = builder.add([0.0, 0.0, 0.0, 0.001, 0.0, 0.0, 0.0, 0.001, 0.0], 5126, "VEC3", 3,
                           target=34962, bounds=True)
    joints = builder.add([0, 0, 0, 0] * 3, 5123, "VEC4", 3, target=34962)
    weights = builder.add([1.0, 0.0, 0.0, 0.0] * 3, 5126, "VEC4", 3, target=34962)
    indices = builder.add([0, 1, 2], 5123, "SCALAR", 3, target=34963)
    document["materials"].append({"name": "Invisible helper", "alphaMode": "BLEND",
                                   "pbrMetallicRoughness": {"baseColorFactor": [0, 0, 0, 0]}})
    document["meshes"].append({"name": "Skeleton helper", "primitives": [{
        "attributes": {"POSITION": position, "JOINTS_0": joints, "WEIGHTS_0": weights},
        "indices": indices, "material": 0,
    }]})
    mesh_node = len(document["nodes"])
    document["nodes"].append({"name": "Skeleton helper", "mesh": 0, "skin": 0})
    document["scenes"][0]["nodes"].append(mesh_node)

    animation = {"name": source["animation"]["name"], "samplers": [], "channels": []}
    frame_count = int(source["animation"]["frame_count"])
    fps = float(source["animation"]["frames_per_second"])
    times = [frame / fps for frame in range(frame_count)]
    time_accessor = builder.add(times, 5126, "SCALAR", frame_count, bounds=True)

    selected: dict[tuple[int, str], dict[str, Any]] = {}
    for entry in source.get("entries", []):
        for channel in entry.get("channels", []):
            track = channel.get("assembled_track") or {}
            binding = channel.get("skeleton_binding")
            if not binding or track.get("status") != "decoded":
                continue
            kind = channel["track_kind_name"]
            path = "translation" if "vector" in kind else "rotation"
            key = (binding["index"], path)
            is_root = kind.startswith("root_")
            if key not in selected or (root_tracks and is_root) or (not root_tracks and not is_root):
                selected[key] = channel

    for (node_index, path), channel in sorted(selected.items()):
        samples = fit_samples(channel["assembled_track"]["samples"], frame_count)
        if path == "rotation":
            # Keep adjacent quaternion signs continuous for interpolation.
            fixed = []
            for sample in samples:
                sample = list(sample)
                if fixed and sum(a*b for a, b in zip(fixed[-1], sample)) < 0:
                    sample = [-value for value in sample]
                fixed.append(sample)
            samples = fixed
        output = builder.add((value for sample in samples for value in sample), 5126,
                             "VEC3" if path == "translation" else "VEC4", frame_count)
        sampler = len(animation["samplers"])
        animation["samplers"].append({"input": time_accessor, "output": output, "interpolation": "LINEAR"})
        animation["channels"].append({"sampler": sampler, "target": {"node": node_index, "path": path}})
    document["animations"].append(animation)

    document["buffers"].append({
        "byteLength": len(builder.data),
        "uri": "data:application/octet-stream;base64," + base64.b64encode(builder.data).decode("ascii"),
    })
    return document


def append_animation(document: dict[str, Any], builder: BufferBuilder,
                     source: dict[str, Any], *, root_tracks: bool = True) -> None:
    animation = {"name": source["animation"]["name"], "samplers": [], "channels": []}
    frame_count = int(source["animation"]["frame_count"])
    fps = float(source["animation"]["frames_per_second"])
    times = [frame / fps for frame in range(frame_count)]
    time_accessor = builder.add(times, 5126, "SCALAR", frame_count, bounds=True)
    selected: dict[tuple[int, str], dict[str, Any]] = {}
    for entry in source.get("entries", []):
        for channel in entry.get("channels", []):
            track = channel.get("assembled_track") or {}
            binding = channel.get("skeleton_binding")
            if not binding or track.get("status") != "decoded":
                continue
            kind = channel["track_kind_name"]
            path = "translation" if "vector" in kind else "rotation"
            key = (binding["index"], path)
            is_root = kind.startswith("root_")
            if key not in selected or (root_tracks and is_root) or (not root_tracks and not is_root):
                selected[key] = channel
    for (node_index, path), channel in sorted(selected.items()):
        samples = fit_samples(channel["assembled_track"]["samples"], frame_count)
        if path == "rotation":
            fixed = []
            for sample in samples:
                sample = list(sample)
                if fixed and sum(a*b for a, b in zip(fixed[-1], sample)) < 0:
                    sample = [-value for value in sample]
                fixed.append(sample)
            samples = fixed
        output = builder.add((value for sample in samples for value in sample), 5126,
                             "VEC3" if path == "translation" else "VEC4", frame_count)
        sampler = len(animation["samplers"])
        animation["samplers"].append({"input": time_accessor, "output": output, "interpolation": "LINEAR"})
        animation["channels"].append({"sampler": sampler, "target": {"node": node_index, "path": path}})
    document["animations"].append(animation)


def export_gltf_batch(sources: list[dict[str, Any]], *, root_tracks: bool = True) -> dict[str, Any]:
    if not sources:
        raise ValueError("At least one bound animation is required.")
    document = export_gltf(sources[0], root_tracks=root_tracks)
    builder = BufferBuilder(document)
    builder.data = bytearray(base64.b64decode(document["buffers"][0]["uri"].split(",", 1)[1]))
    for source in sources[1:]:
        append_animation(document, builder, source, root_tracks=root_tracks)
    document["buffers"][0] = {
        "byteLength": len(builder.data),
        "uri": "data:application/octet-stream;base64," + base64.b64encode(builder.data).decode("ascii"),
    }
    document.setdefault("extras", {})["animationCount"] = len(document["animations"])
    document["extras"]["animationNames"] = [animation["name"] for animation in document["animations"]]
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bound_animation", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--regular-root-tracks", action="store_true",
                        help="Prefer ordinary root tracks instead of root-motion tracks.")
    args = parser.parse_args()
    source = json.loads(args.bound_animation.read_text(encoding="utf-8"))
    document = export_gltf(source, root_tracks=not args.regular_root_tracks)
    args.output.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Exported {len(document['nodes'])-1} bones and {len(document['animations'][0]['channels'])} tracks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
