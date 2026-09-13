#!/usr/bin/env python3
"""Inspect decompressed RDR1 PC/Switch WFT geometry and skin streams."""

from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path
from typing import Any

TYPE_SIZES = [2, 4, 6, 8, 4, 8, 12, 16, 4, 4, 4, 0, 0, 0, 4, 8]


class Reader:
    def __init__(self, data: bytes): self.data, self.pos = data, 0
    def seek(self, position: int): self.pos = position
    def read(self, size: int) -> bytes:
        value = self.data[self.pos:self.pos+size]
        if len(value) != size: raise ValueError(f"read beyond resource at 0x{self.pos:X}")
        self.pos += size
        return value
    def u8(self): return self.read(1)[0]
    def u16(self): return struct.unpack("<H", self.read(2))[0]
    def u32(self): return struct.unpack("<I", self.read(4))[0]
    def u64(self): return struct.unpack("<Q", self.read(8))[0]
    def f32(self): return struct.unpack("<f", self.read(4))[0]
    def ptr(self): return self.u32() & 0x0FFFFFFF


def dec3n(value: int) -> list[float]:
    result = []
    for shift in (0, 10, 20):
        raw = (value >> shift) & 0x3FF
        negative = bool(raw & 0x200)
        magnitude = ((~raw if negative else raw) & 0x1FF) / 511.0
        result.append(-magnitude if negative else magnitude)
    return result


def half_be(data: bytes) -> float:
    return struct.unpack(">e", data)[0]


def read_cstring(reader: Reader, pointer: int) -> str:
    if not pointer or pointer >= len(reader.data):
        return ""
    end = reader.data.find(b"\0", pointer)
    if end < 0:
        end = min(pointer + 256, len(reader.data))
    return reader.data[pointer:end].decode("ascii", errors="replace")


def shader_textures(reader: Reader, drawable: int) -> list[list[str]]:
    reader.seek(drawable + 8)
    shader_group = reader.ptr()
    if not shader_group:
        return []
    reader.seek(shader_group + 8)
    shader_array, shader_count = reader.ptr(), reader.u16()
    reader.seek(shader_array)
    shader_pointers = [reader.ptr() for _ in range(shader_count)]
    result: list[list[str]] = []
    for shader in shader_pointers:
        reader.seek(shader)
        parameters = reader.ptr()
        reader.seek(shader + 8)
        parameter_count = reader.u8()
        names: list[str] = []
        reader.seek(parameters)
        descriptors = [(reader.u8(), reader.u8(), reader.u8(), reader.u8(), reader.ptr())
                       for _ in range(parameter_count)]
        for data_type, _, _, _, data_pointer in descriptors:
            if data_type != 0 or not data_pointer:
                continue
            reader.seek(data_pointer + 0x18)
            name = read_cstring(reader, reader.ptr())
            if name and name not in names:
                names.append(name)
        result.append(names)
    return result


def geometry_pointers(reader: Reader) -> tuple[int, list[tuple[int, int]]]:
    reader.seek(0xB4)
    drawable = reader.ptr()
    reader.seek(drawable + 0x40)
    model_roots = [reader.ptr() for _ in range(4)]
    pointers: list[tuple[int, int]] = []
    for model_root in dict.fromkeys(pointer for pointer in model_roots if pointer):
        reader.seek(model_root)
        mesh_array, mesh_count = reader.ptr(), reader.u16()
        reader.seek(mesh_array)
        meshes = [reader.ptr() for _ in range(mesh_count)]
        for mesh in meshes:
            reader.seek(mesh + 4)
            array, count = reader.ptr(), reader.u16()
            reader.seek(mesh + 0x10)
            shader_map = reader.ptr()
            reader.seek(array)
            geometry_items = [reader.ptr() for _ in range(count)]
            reader.seek(shader_map)
            shader_ids = [reader.u16() for _ in range(count)] if shader_map else [0] * count
            pointers.extend(zip(geometry_items, shader_ids))
    return drawable, pointers


def inspect(data: bytes) -> dict[str, Any]:
    reader = Reader(data)
    geometries = []
    drawable, geometry_items = geometry_pointers(reader)
    shaders = shader_textures(reader, drawable)
    for geometry_index, (pointer, shader_id) in enumerate(geometry_items):
        reader.seek(pointer + 0x0C)
        vertex_buffers = [reader.ptr() for _ in range(4)]
        index_buffers = [reader.ptr() for _ in range(4)]
        index_count, face_count = reader.u32(), reader.u32()
        vertex_count = reader.u16()
        primitive_type, _ = reader.u8(), reader.u8()
        joint_map_pointer = reader.ptr()
        vertex_stride, joint_count = reader.u16(), reader.u16()
        data_offset_raw = reader.u32()
        legacy_vertex_data = data_offset_raw & 0x0FFFFFFF

        reader.seek(joint_map_pointer)
        joint_map = [reader.u16() for _ in range(joint_count)] if joint_map_pointer else []
        reader.seek(vertex_buffers[0] + 0x10)
        vertex_data = reader.ptr()
        reader.seek(vertex_buffers[0] + 0x18)
        vertex_format = reader.ptr()
        reader.seek(vertex_format)
        mask, declared_size = reader.u32(), reader.u8()
        reader.read(3)
        fvf = reader.u64()

        usages = [(usage, (fvf >> (usage * 4)) & 0xF) for usage in range(16) if mask & (1 << usage)]
        layout_size = sum(TYPE_SIZES[kind] for _, kind in usages)
        positions, normals, texcoords, colors, tangents, weights, joints = [], [], [], [], [], [], []
        reader.seek(vertex_data)
        for vertex in range(vertex_count):
            start = reader.pos
            vertex_weights = [1.0, 0.0, 0.0, 0.0]
            vertex_joints = [0, 0, 0, 0]
            for usage, kind in usages:
                size = TYPE_SIZES[kind]
                if usage == 0:
                    positions.append([reader.f32(), reader.f32(), reader.f32()])
                elif usage == 1 and size == 4:
                    raw = reader.read(4)
                    vertex_weights = [value / 255.0 for value in raw]
                elif usage == 2 and size == 4:
                    raw = reader.read(4)
                    vertex_joints = [joint_map[value] if value < len(joint_map) else 0 for value in raw]
                elif usage == 3 and size == 4:
                    normals.append(dec3n(reader.u32()))
                elif usage == 4 and size == 4:
                    # Stored as a little-endian ARGB uint (bytes on disk are B, G, R, A).
                    blue, green, red, alpha = reader.read(4)
                    colors.append([red / 255.0, green / 255.0, blue / 255.0, alpha / 255.0])
                elif usage == 6 and size == 4:
                    texcoords.append([half_be(reader.read(2)), half_be(reader.read(2))])
                elif usage == 14 and size == 4:
                    tangent = dec3n(reader.u32())
                    tangents.append([*tangent, 1.0])
                else:
                    reader.read(size)
            if reader.pos - start != layout_size:
                raise ValueError("vertex layout size mismatch")
            weights.append(vertex_weights)
            joints.append(vertex_joints)

        reader.seek(index_buffers[0] + 8)
        index_data = reader.ptr()
        reader.seek(index_data)
        indices = list(struct.unpack("<" + "H" * (face_count * 3), reader.read(face_count * 6)))
        finite = all(math.isfinite(value) for position in positions for value in position)
        geometries.append({
            "index": geometry_index, "pointer_hex": f"0x{pointer:X}", "vertex_count": vertex_count,
            "shader_id": shader_id,
            "shader_textures": shaders[shader_id] if shader_id < len(shaders) else [],
            "index_count": index_count, "face_count": face_count, "primitive_type": primitive_type,
            "vertex_stride": vertex_stride, "declared_vertex_size": declared_size, "layout_size": layout_size,
            "vertex_data_hex": f"0x{vertex_data:X}", "legacy_vertex_data_hex": f"0x{legacy_vertex_data:X}",
            "mask_hex": f"0x{mask:08X}", "fvf_indices_hex": f"0x{fvf:016X}", "usages": usages,
            "joint_count": joint_count, "joint_map": joint_map, "positions_finite": finite,
            "max_index": max(indices, default=-1),
            "weight_sum_range": [min(map(sum, weights), default=0), max(map(sum, weights), default=0)],
            "used_skeleton_joints": sorted({joint for row in joints for joint in row}),
            "positions": positions, "normals": normals, "texcoords": texcoords,
            "colors": colors, "tangents": tangents,
            "blend_weights": weights, "blend_indices": joints, "indices": indices,
        })
    return {"schema": "rdr1-animation-studio.wft-geometry.v1", "geometry_count": len(geometries), "geometries": geometries}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resource", type=Path, help="Decompressed resource data from RpfCli dump-resource.")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.resource.read_bytes())
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Decoded {result['geometry_count']} geometries.")
    for geometry in result["geometries"]:
        print(f"  {geometry['index']}: {geometry['vertex_count']} vertices, {geometry['face_count']} faces, "
              f"{geometry['joint_count']} joints, weights {geometry['weight_sum_range']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
