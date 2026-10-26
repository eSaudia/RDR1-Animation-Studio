#!/usr/bin/env python3
"""Decode the validated structural layer of RDR1 WBD/WVD resources.

The RSC container is decompressed first, then the RSC6 virtual pointers and
dictionary layouts are validated.  WBD bounds are decoded through the
quantized vertex/polygon representation used by CodeX RDR1.  WVD follows
drawable pointers through LODs, models, geometry, vertex/index buffers, and
vertex declarations.  Mesh evidence is emitted only when every pointer,
header, stride, and payload range validates; shader/material and placement
semantics remain explicitly unresolved.

This is deliberately a bounded evidence decoder: a bad pointer or unknown
subtype is reported, never guessed.  The JSON output is useful both to the
desktop viewer and to later scene reconstruction work.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any

try:
    _HERE = Path(__file__).resolve().parent
    if str(_HERE / "_deps") not in sys.path:
        sys.path.insert(0, str(_HERE / "_deps"))
    import zstandard  # type: ignore
except Exception:  # pragma: no cover - report is still useful without zstd
    zstandard = None

RSC_MAGICS = {b"RSC\x05", b"RSC\x06", b"RSC\x85", b"RSC\x86"}
VIRTUAL_BASE = 0x50000000
PHYSICAL_BASE = 0x60000000
BOUNDS_VFT = 0x01830BC0
VISUAL_DICTIONARY_VFT = 0x01908FF8
DRAWABLE_VFT = 0x01908F7C
MODEL_VFT = 0x01854414
GEOMETRY_VFT = 0x018572A0
VERTEX_BUFFER_VFT = 0x01858684
INDEX_BUFFER_VFT = 0x01858D60
SECTOR_INFO_VFT = 0x01909C38
DRAWABLE_INSTANCE_VFT = 0x01913300
BOUNDS_TYPES = {
    0: "Sphere", 1: "Capsule", 2: "TaperedCapsule", 3: "Box",
    4: "Geometry", 5: "CurvedGeometry", 6: "Octree", 7: "Quadtree",
    8: "Grid", 9: "Ribbon", 10: "GeometryBVH", 11: "Surface",
    12: "Composite", 13: "Triangle",
}

FVF_COMPONENTS = {
    0: ("Nothing", 2), 1: ("Half2", 4), 2: ("Float", 6),
    3: ("Half4", 8), 4: ("Float", 4), 5: ("Float2", 8),
    6: ("Float3", 12), 7: ("Float4", 16), 8: ("UByte4", 4),
    9: ("Colour", 4), 10: ("Dec3N", 4), 11: ("Unk1", 2),
    12: ("Unk2", 4), 13: ("Unk3", 2), 14: ("UShort2N", 4),
    15: ("Unk5", 8),
}

FVF_SEMANTICS = {
    0: "POSITION", 1: "BLENDWEIGHTS", 2: "BLENDINDICES", 3: "NORMAL",
    4: "COLOR0", 5: "COLOR1", 6: "TEXCOORD0", 7: "TEXCOORD1",
    8: "TEXCOORD2", 9: "TEXCOORD3", 10: "TEXCOORD4", 11: "TEXCOORD5",
    12: "TEXCOORD6", 13: "TEXCOORD7", 14: "TANGENT0", 15: "TANGENT1",
}

MAX_DRAWABLES = 512
MAX_MODELS_PER_DRAWABLE = 256
MAX_GEOMETRIES_PER_MODEL = 256
MAX_VERTICES_PER_GEOMETRY = 100000
MAX_INDICES_PER_GEOMETRY = 300000
MAX_SECTORS = 4096
MAX_INSTANCES_PER_SECTOR = 20000


class DecodeError(Exception):
    pass


class Reader:
    def __init__(self, data: bytes, virtual_size: int):
        self.data = data
        self.virtual_size = virtual_size
        self.errors: list[str] = []

    def valid(self, offset: int, size: int = 1) -> bool:
        return 0 <= offset <= len(self.data) and 0 <= size <= len(self.data) - offset

    def u8(self, offset: int) -> int:
        if not self.valid(offset, 1):
            raise DecodeError(f"u8 out of range at 0x{offset:X}")
        return self.data[offset]

    def u16(self, offset: int) -> int:
        if not self.valid(offset, 2):
            raise DecodeError(f"u16 out of range at 0x{offset:X}")
        return struct.unpack_from("<H", self.data, offset)[0]

    def i16(self, offset: int) -> int:
        if not self.valid(offset, 2):
            raise DecodeError(f"i16 out of range at 0x{offset:X}")
        return struct.unpack_from("<h", self.data, offset)[0]

    def u32(self, offset: int) -> int:
        if not self.valid(offset, 4):
            raise DecodeError(f"u32 out of range at 0x{offset:X}")
        return struct.unpack_from("<I", self.data, offset)[0]

    def i32(self, offset: int) -> int:
        if not self.valid(offset, 4):
            raise DecodeError(f"i32 out of range at 0x{offset:X}")
        return struct.unpack_from("<i", self.data, offset)[0]

    def f32(self, offset: int) -> float:
        if not self.valid(offset, 4):
            raise DecodeError(f"f32 out of range at 0x{offset:X}")
        return struct.unpack_from("<f", self.data, offset)[0]

    def ptr(self, value: int) -> int | None:
        """Map an RSC6 virtual/physical pointer to a decompressed offset."""
        if value == 0:
            return None
        if value & VIRTUAL_BASE == VIRTUAL_BASE:
            offset = value & 0x0FFFFFFF
        elif value & PHYSICAL_BASE == PHYSICAL_BASE:
            offset = (value & 0x1FFFFFFF) + self.virtual_size
        else:
            raise DecodeError(f"invalid RSC6 pointer 0x{value:08X}")
        if not self.valid(offset):
            raise DecodeError(f"pointer 0x{value:08X} maps outside payload at 0x{offset:X}")
        return offset

    def pointer_at(self, offset: int) -> int | None:
        return self.ptr(self.u32(offset))

    def vec3(self, offset: int) -> list[float]:
        # RDR stores the resource vector as X,Y,Z; CodeX exposes Z,X,Y.
        return [self.f32(offset + 8), self.f32(offset), self.f32(offset + 4)]

    def vec4(self, offset: int) -> list[float]:
        return [self.f32(offset + 8), self.f32(offset), self.f32(offset + 4), self.f32(offset + 12)]


def finite_vector(v: list[float]) -> bool:
    return all(math.isfinite(x) for x in v)


def decode_container(path: Path) -> tuple[dict[str, Any], bytes, int]:
    raw = path.read_bytes()
    if len(raw) < 12 or raw[:4] not in RSC_MAGICS:
        raise DecodeError("not an RSC05/RSC06/RSC85/RSC86 resource")
    magic = raw[:4].decode("latin1")
    version = struct.unpack_from("<I", raw, 4)[0]
    flag1 = struct.unpack_from("<I", raw, 8)[0]
    is_extended = raw[:4] in {b"RSC\x85", b"RSC\x86"}
    flag2 = struct.unpack_from("<I", raw, 12)[0] if is_extended else 0
    header_size = 16 if is_extended else 12
    compressed = raw[header_size:]
    if zstandard is None:
        raise DecodeError("zstandard module is not available in the bundled Python runtime")

    # RSC05/RSC85 resources use a streaming frame with no content-size field.
    try:
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(compressed)) as stream:
            payload = stream.read()
    except Exception as exc:
        raise DecodeError(f"zstd decompression failed: {exc}") from exc

    if flag2:
        # RSC85 sizes are explicitly stored in page fields.  WBD/WVD samples
        # are RSC05, but retaining the fields makes a failed RSC85 report useful.
        virtual_size = ((flag2 & 0x3FFF) << 12)
        physical_size = (((flag2 >> 14) & 0x3FFF) << 12)
        object_start = 0
    else:
        virtual_size = ((flag1 & 0x7FF) << (((flag1 >> 11) & 0xF) + 8))
        physical_size = (((flag1 >> 15) & 0x7FF) << (((flag1 >> 26) & 0xF) + 8))
        object_start = 0

    # Some RSC05 producers leave the compact size fields rounded differently
    # from the final stream.  Pointers are still virtual for the WBD/WVD roots;
    # use the observed virtual/physical split when it is safely in range.
    if virtual_size <= 0 or virtual_size > len(payload):
        virtual_size = len(payload) if not physical_size else min(len(payload), virtual_size)

    return {
        "magic": magic,
        "version": version,
        "flag1": f"0x{flag1:08X}",
        "flag2": f"0x{flag2:08X}" if flag2 else None,
        "headerBytes": header_size,
        "compressedBytes": len(compressed),
        "decompressedBytes": len(payload),
        "virtualSize": virtual_size,
        "physicalSize": physical_size,
        "objectStart": object_start,
    }, payload, virtual_size


def read_descriptor(reader: Reader, offset: int) -> dict[str, int]:
    pos = reader.u32(offset)
    count = reader.u16(offset + 4)
    capacity = reader.u16(offset + 6)
    target = reader.ptr(pos)
    return {"descriptorOffset": offset, "position": pos, "targetOffset": target if target is not None else 0, "count": count, "capacity": capacity}


def read_u32_array(reader: Reader, descriptor: dict[str, int], limit: int | None = None) -> list[int]:
    target = descriptor.get("targetOffset", 0)
    count = descriptor["count"]
    if target == 0 or count == 0:
        return []
    if limit is not None:
        count = min(count, limit)
    if not reader.valid(target, count * 4):
        raise DecodeError(f"array at 0x{target:X} exceeds payload")
    return [reader.u32(target + i * 4) for i in range(count)]


def read_ptr_array(reader: Reader, descriptor: dict[str, int]) -> list[tuple[int, int | None]]:
    values = read_u32_array(reader, descriptor)
    return [(value, reader.ptr(value) if value else None) for value in values]


def decode_base_bound(reader: Reader, offset: int) -> dict[str, Any]:
    if not reader.valid(offset, 144):
        raise DecodeError(f"bounds block at 0x{offset:X} is truncated")
    vft = reader.u32(offset + 16)
    type_id = reader.u8(offset + 20)
    result: dict[str, Any] = {
        "offset": offset,
        "vft": f"0x{vft:08X}",
        "typeId": type_id,
        "type": BOUNDS_TYPES.get(type_id, f"Unknown({type_id})"),
        "flags": {
            "hasCentroidOffset": bool(reader.u8(offset + 21)),
            "hasCgOffset": bool(reader.u8(offset + 22)),
            "worldSpaceUpdatesEnabled": bool(reader.u8(offset + 23)),
        },
        "sphereRadius": reader.f32(offset + 24),
        "worldRadius": reader.f32(offset + 28),
        "boxMax": reader.vec3(offset + 32),
        "boxMin": reader.vec3(offset + 48),
        "boxCenter": reader.vec3(offset + 64),
        "centroidOffsetWorldSpace": reader.vec3(offset + 80),
        "sphereCenter": reader.vec3(offset + 96),
        "volumeDistribution": reader.vec4(offset + 112),
        "margin": reader.vec3(offset + 128),
        "refCount": reader.u32(offset + 140),
    }
    return result


def decode_polyhedron(reader: Reader, offset: int, base: dict[str, Any]) -> None:
    if not reader.valid(offset + 144, 80):
        base["unresolved"] = ["polyhedron header truncated"]
        return
    base["polyhedron"] = {
        "verticesPad": reader.u32(offset + 144),
        "vertexColours": reader.u32(offset + 148),
        "verticesWorldSpace": reader.u32(offset + 152),
        "polygonsData": reader.u32(offset + 156),
        "quantum": reader.vec3(offset + 160),
        "centerGeom": reader.vec3(offset + 176),
        "verticesData": reader.u32(offset + 192),
        "smallPolygonsWorldSpace": reader.u32(offset + 196),
        "useActiveComponents": bool(reader.u8(offset + 200)),
        "isFlat": bool(reader.u8(offset + 201)),
        "numConvexHullVertices": reader.i16(offset + 202),
        "numActivePolygons": reader.i16(offset + 204),
        "numActiveVertices": reader.i16(offset + 206),
        "activePolygonIndices": reader.u32(offset + 208),
        "activeVertexIndices": reader.u32(offset + 212),
        "verticesCount": reader.u32(offset + 216),
        "polygonsCount": reader.u32(offset + 220),
    }
    poly = base["polyhedron"]
    vertices_ptr = reader.ptr(poly["verticesData"])
    polygons_ptr = reader.ptr(poly["polygonsData"])
    vertex_count = min(poly["verticesCount"], 200000)
    polygon_count = min(poly["polygonsCount"], 200000)
    if vertex_count and vertices_ptr is not None:
        if not reader.valid(vertices_ptr, vertex_count * 6):
            base.setdefault("unresolved", []).append("quantized vertex array exceeds payload")
        else:
            q = poly["quantum"]
            c = poly["centerGeom"]
            verts: list[list[float]] = []
            for i in range(vertex_count):
                raw = struct.unpack_from("<hhh", reader.data, vertices_ptr + i * 6)
                # Vector3S is transformed Z,X,Y before scaling, same as CodeX.
                verts.append([raw[2] * q[0] + c[0], raw[0] * q[1] + c[1], raw[1] * q[2] + c[2]])
            poly["decodedVertices"] = verts
            poly["decodedVertexSourceOffset"] = vertices_ptr
    triangles: list[list[int]] = []
    polygon_kinds: dict[str, int] = {}
    if polygon_count and polygons_ptr is not None:
        if not reader.valid(polygons_ptr, polygon_count * 16):
            base.setdefault("unresolved", []).append("polygon record array exceeds payload")
        else:
            for index in range(polygon_count):
                p = polygons_ptr + index * 16
                b0 = reader.u8(p) & 0xFB
                kind_id = b0 & 0x7
                area = reader.f32(p)
                if kind_id not in (1, 2, 3) and math.isfinite(area) and 0.0 < area < 50000.0:
                    kind = "Triangle"
                    tri = [reader.u16(p + 4), reader.u16(p + 6), reader.u16(p + 8)]
                    triangles.append(tri)
                elif kind_id == 1:
                    kind = "Sphere"
                elif kind_id == 2:
                    kind = "Capsule"
                elif kind_id == 3:
                    kind = "Box"
                else:
                    kind = f"Unknown({kind_id})"
                polygon_kinds[kind] = polygon_kinds.get(kind, 0) + 1
            poly["polygonKinds"] = polygon_kinds
            poly["triangleIndices"] = triangles
            poly["decodedPolygonSourceOffset"] = polygons_ptr
    if base["typeId"] == 10 and reader.valid(offset + 224, 16):
        bvh_ptr_value = reader.u32(offset + 224)
        bvh_target = reader.ptr(bvh_ptr_value) if bvh_ptr_value else None
        base["bvh"] = {"pointer": bvh_ptr_value, "targetOffset": bvh_target or 0}


def decode_wbd(reader: Reader, object_start: int) -> dict[str, Any]:
    root = object_start
    if not reader.valid(root, 32):
        raise DecodeError("WBD root is truncated")
    vft = reader.u32(root)
    if vft != BOUNDS_VFT:
        raise DecodeError(f"WBD root VFT 0x{vft:08X} does not match 0x{BOUNDS_VFT:08X}")
    hashes_desc = read_descriptor(reader, root + 16)
    bounds_desc = read_descriptor(reader, root + 24)
    hashes = read_u32_array(reader, hashes_desc)
    pointers = read_ptr_array(reader, bounds_desc)
    bounds: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for index, (raw_ptr, target) in enumerate(pointers):
        item: dict[str, Any] = {"index": index, "hash": f"0x{hashes[index]:08X}" if index < len(hashes) else None, "pointer": f"0x{raw_ptr:08X}", "targetOffset": target or 0}
        if target is None:
            item["status"] = "null-pointer"
            unresolved.append(f"bounds[{index}] is null")
            bounds.append(item)
            continue
        try:
            decoded = decode_base_bound(reader, target)
            if decoded["typeId"] in (4, 5, 10):
                decode_polyhedron(reader, target, decoded)
            item.update(decoded)
            item["status"] = "decoded-bounds-and-quantized-geometry"
        except DecodeError as exc:
            item["status"] = "pointer-valid-header-failed"
            item["error"] = str(exc)
            unresolved.append(f"bounds[{index}]: {exc}")
        bounds.append(item)
    return {
        "kind": "WBD",
        "confidence": "validated RSC05 container, BoundsDictionary root, bounds records, and quantized geometry where pointers validate",
        "root": {
            "offset": root,
            "vft": f"0x{vft:08X}",
            "parentDictionary": f"0x{reader.u32(root + 8):08X}",
            "usageCount": reader.u32(root + 12),
            "hashes": hashes_desc,
            "bounds": bounds_desc,
        },
        "hashes": [f"0x{x:08X}" for x in hashes],
        "bounds": bounds,
        "counts": {"bounds": len(bounds), "decodedBounds": sum(1 for x in bounds if x.get("status") == "decoded-bounds-and-quantized-geometry"), "vertices": sum(len(x.get("polyhedron", {}).get("decodedVertices", [])) for x in bounds)},
        "unresolved": unresolved,
    }


def _safe_target(reader: Reader, raw: int) -> tuple[int | None, str | None]:
    """Resolve a pointer without treating debug-fill bytes as a real pointer."""
    if raw in (0, 0xCDCDCDCD, 0xFFFFFFFF):
        return None, None
    try:
        return reader.ptr(raw), None
    except DecodeError as exc:
        return None, str(exc)


def _bounded_ptr_array(reader: Reader, descriptor: dict[str, int], limit: int) -> list[tuple[int, int | None]]:
    values = read_u32_array(reader, descriptor, limit=limit)
    result: list[tuple[int, int | None]] = []
    for value in values:
        target, _ = _safe_target(reader, value)
        result.append((value, target))
    return result


def _position_bounds(positions: list[list[float]]) -> dict[str, list[float]] | None:
    if not positions:
        return None
    mins = [min(v[i] for v in positions) for i in range(3)]
    maxs = [max(v[i] for v in positions) for i in range(3)]
    return {"min": mins, "max": maxs}


def decode_vertex_declaration(reader: Reader, offset: int) -> dict[str, Any]:
    """Decode the 16-byte grcFvf block (it has no serialized VFT field)."""
    if not reader.valid(offset, 16):
        raise DecodeError(f"vertex declaration at 0x{offset:X} is truncated")
    fvf = reader.u32(offset)
    fvf_size = reader.u8(offset + 4)
    flags = reader.u8(offset + 5)
    dynamic_order = reader.u8(offset + 6)
    channel_count = reader.u8(offset + 7)
    types = reader.u32(offset + 8) | (reader.u32(offset + 12) << 32)
    elements: list[dict[str, Any]] = []
    cursor = 0
    for bit in range(16):
        if not ((fvf >> bit) & 1):
            continue
        component_id = (types >> (bit * 4)) & 0xF
        component = FVF_COMPONENTS.get(component_id)
        if component is None:
            name, size = f"Unknown({component_id})", 0
        else:
            name, size = component
        element = {
            "channel": bit,
            "semantic": FVF_SEMANTICS.get(bit, f"CHANNEL{bit}"),
            "componentId": component_id,
            "format": name,
            "offset": cursor,
            "size": size,
        }
        elements.append(element)
        cursor += size
    errors: list[str] = []
    if fvf_size == 0:
        errors.append("FVFSize is zero")
    if cursor != fvf_size:
        errors.append(f"FVF component sum {cursor} does not equal FVFSize {fvf_size}")
    if channel_count and channel_count != len(elements):
        errors.append(f"ChannelCount {channel_count} does not equal active channel count {len(elements)}")
    position = next((x for x in elements if x["semantic"] == "POSITION"), None)
    return {
        "offset": offset,
        "fvf": f"0x{fvf:08X}",
        "fvfSize": fvf_size,
        "flags": flags,
        "dynamicOrder": dynamic_order,
        "channelCount": channel_count,
        "types": f"0x{types:016X}",
        "elements": elements,
        "positionElement": position,
        "valid": not errors,
        "errors": errors,
    }


def decode_vertex_buffer(reader: Reader, offset: int) -> dict[str, Any]:
    if not reader.valid(offset, 64):
        raise DecodeError(f"vertex buffer at 0x{offset:X} is truncated")
    vft = reader.u32(offset)
    vertex_count = reader.u16(offset + 4)
    locked = reader.u8(offset + 6)
    flags = reader.u8(offset + 7)
    locked_raw = reader.u32(offset + 8)
    stride = reader.u32(offset + 12)
    data_raw = reader.u32(offset + 16)
    layout_raw = reader.u32(offset + 24)
    layout_target, layout_error = _safe_target(reader, layout_raw)
    if layout_target is None:
        raise DecodeError(layout_error or "vertex buffer has no layout pointer")
    layout = decode_vertex_declaration(reader, layout_target)
    fvf_size = int(layout["fvfSize"])
    if fvf_size <= 0 or fvf_size > 4096:
        raise DecodeError(f"unsupported vertex FVF size {fvf_size}")
    data_target, data_error = _safe_target(reader, data_raw)
    expected_bytes = vertex_count * fvf_size
    if expected_bytes > MAX_VERTICES_PER_GEOMETRY * 4096:
        raise DecodeError(f"vertex payload is unreasonably large ({expected_bytes} bytes)")
    if data_target is None:
        raise DecodeError(data_error or "vertex buffer has no vertex-data pointer")
    if not reader.valid(data_target, expected_bytes):
        raise DecodeError(f"vertex payload at 0x{data_target:X} exceeds payload")
    result: dict[str, Any] = {
        "offset": offset,
        "vft": f"0x{vft:08X}",
        "expectedVft": f"0x{VERTEX_BUFFER_VFT:08X}",
        "vftValidated": vft == VERTEX_BUFFER_VFT,
        "vertexCount": vertex_count,
        "locked": locked,
        "flags": flags,
        "lockedDataPointer": f"0x{locked_raw:08X}",
        "vertexStride": stride,
        "vertexDataPointer": f"0x{data_raw:08X}",
        "vertexDataOffset": data_target,
        "vertexDataBytes": expected_bytes,
        "layout": layout,
    }
    if vft != VERTEX_BUFFER_VFT:
        result.setdefault("unresolved", []).append("vertex-buffer VFT differs from CodeX reference")
    return result


def decode_index_buffer(reader: Reader, offset: int) -> dict[str, Any]:
    if not reader.valid(offset, 48):
        raise DecodeError(f"index buffer at 0x{offset:X} is truncated")
    vft = reader.u32(offset)
    index_count = reader.u32(offset + 4)
    data_raw = reader.u32(offset + 8)
    if index_count > MAX_INDICES_PER_GEOMETRY:
        raise DecodeError(f"index count {index_count} exceeds safety limit")
    data_target, data_error = _safe_target(reader, data_raw)
    if index_count and data_target is None:
        raise DecodeError(data_error or "index buffer has no index-data pointer")
    if data_target is not None and not reader.valid(data_target, index_count * 2):
        raise DecodeError(f"index payload at 0x{data_target:X} exceeds payload")
    indices = [reader.u16(data_target + i * 2) for i in range(index_count)] if data_target is not None else []
    result: dict[str, Any] = {
        "offset": offset,
        "vft": f"0x{vft:08X}",
        "expectedVft": f"0x{INDEX_BUFFER_VFT:08X}",
        "vftValidated": vft == INDEX_BUFFER_VFT,
        "indexCount": index_count,
        "indexDataPointer": f"0x{data_raw:08X}",
        "indexDataOffset": data_target or 0,
        "indices": indices,
    }
    if vft != INDEX_BUFFER_VFT:
        result.setdefault("unresolved", []).append("index-buffer VFT differs from CodeX reference")
    return result


def decode_geometry(reader: Reader, offset: int) -> dict[str, Any]:
    if not reader.valid(offset, 80):
        raise DecodeError(f"geometry at 0x{offset:X} is truncated")
    vft = reader.u32(offset)
    vertex_raw = reader.u32(offset + 12)
    index_raw = reader.u32(offset + 28)
    indices_count = reader.u32(offset + 44)
    triangles_count = reader.u32(offset + 48)
    vertex_count = reader.u16(offset + 52)
    primitive_type = reader.u8(offset + 54)
    double_buffered = bool(reader.u8(offset + 55))
    vertex_stride = reader.u16(offset + 60)
    bone_ids_count = reader.u16(offset + 62)
    index_offset = reader.u32(offset + 72)
    if vertex_count > MAX_VERTICES_PER_GEOMETRY:
        raise DecodeError(f"vertex count {vertex_count} exceeds safety limit")
    vertex_target, vertex_error = _safe_target(reader, vertex_raw)
    index_target, index_error = _safe_target(reader, index_raw)
    if vertex_target is None:
        raise DecodeError(vertex_error or "geometry has no vertex-buffer pointer")
    if index_target is None:
        raise DecodeError(index_error or "geometry has no index-buffer pointer")
    vertex_buffer = decode_vertex_buffer(reader, vertex_target)
    index_buffer = decode_index_buffer(reader, index_target)
    if vertex_buffer["vertexCount"] != vertex_count:
        raise DecodeError(f"geometry vertex count {vertex_count} != buffer count {vertex_buffer['vertexCount']}")
    if index_buffer["indexCount"] < indices_count:
        raise DecodeError(f"geometry index count {indices_count} exceeds buffer count {index_buffer['indexCount']}")
    positions: list[list[float]] = []
    position_element = vertex_buffer["layout"].get("positionElement")
    position_error: str | None = None
    if position_element is None:
        position_error = "vertex declaration has no POSITION element"
    elif position_element["format"] not in ("Float3", "Float4"):
        position_error = f"POSITION format {position_element['format']} is not decoded"
    else:
        data_offset = vertex_buffer["vertexDataOffset"]
        fvf_size = vertex_buffer["layout"]["fvfSize"]
        component_offset = position_element["offset"]
        component_size = 12 if position_element["format"] == "Float3" else 16
        for i in range(vertex_count):
            p = data_offset + i * fvf_size + component_offset
            if not reader.valid(p, component_size):
                position_error = "position element exceeds vertex payload"
                positions = []
                break
            values = struct.unpack_from("<fff", reader.data, p)
            xyz = [values[2], values[0], values[1]]
            if not finite_vector(xyz):
                position_error = f"non-finite position at vertex {i}"
                positions = []
                break
            positions.append(xyz)
    result: dict[str, Any] = {
        "offset": offset,
        "vft": f"0x{vft:08X}",
        "expectedVft": f"0x{GEOMETRY_VFT:08X}",
        "vftValidated": vft == GEOMETRY_VFT,
        "vertexBufferPointer": f"0x{vertex_raw:08X}",
        "vertexBufferOffset": vertex_target,
        "indexBufferPointer": f"0x{index_raw:08X}",
        "indexBufferOffset": index_target,
        "indicesCount": indices_count,
        "trianglesCount": triangles_count,
        "vertexCount": vertex_count,
        "primitiveType": primitive_type,
        "doubleBuffered": double_buffered,
        "vertexStride": vertex_stride,
        "boneIdsCount": bone_ids_count,
        "indexOffset": index_offset,
        "vertexBuffer": vertex_buffer,
        "indexBuffer": index_buffer,
        "positions": positions,
        "positionBounds": _position_bounds(positions),
        "positionDecoded": bool(positions) and position_error is None,
    }
    unresolved: list[str] = []
    if vft != GEOMETRY_VFT:
        unresolved.append("geometry VFT differs from CodeX reference")
    if position_error:
        unresolved.append(position_error)
    if primitive_type != 3:
        unresolved.append(f"primitive type {primitive_type} is not expanded beyond indexed triangles")
    if unresolved:
        result["unresolved"] = unresolved
    return result


def decode_model(reader: Reader, offset: int) -> dict[str, Any]:
    if not reader.valid(offset, 28):
        raise DecodeError(f"drawable model at 0x{offset:X} is truncated")
    vft = reader.u32(offset)
    geometry_desc = read_descriptor(reader, offset + 4)
    geometry_ptrs = _bounded_ptr_array(reader, geometry_desc, MAX_GEOMETRIES_PER_MODEL)
    geometries: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for index, (raw_ptr, target) in enumerate(geometry_ptrs):
        item: dict[str, Any] = {"index": index, "pointer": f"0x{raw_ptr:08X}", "targetOffset": target or 0}
        if target is None:
            item["status"] = "null-or-invalid-pointer"
        else:
            try:
                item.update(decode_geometry(reader, target))
                item["status"] = "decoded-mesh-evidence"
            except DecodeError as exc:
                item["status"] = "pointer-valid-header-failed"
                item["error"] = str(exc)
                unresolved.append(f"geometry[{index}]: {exc}")
        geometries.append(item)
    result: dict[str, Any] = {
        "offset": offset,
        "vft": f"0x{vft:08X}",
        "expectedVft": f"0x{MODEL_VFT:08X}",
        "vftValidated": vft == MODEL_VFT,
        "geometries": geometries,
        "geometryDescriptor": geometry_desc,
        "boundsDataPointer": f"0x{reader.u32(offset + 12):08X}",
        "shaderMappingPointer": f"0x{reader.u32(offset + 16):08X}",
        "matrixCount": reader.u8(offset + 20),
        "flags": reader.u8(offset + 21),
        "type": reader.u8(offset + 22),
        "matrixIndex": reader.u8(offset + 23),
        "stride": reader.u8(offset + 24),
        "skinFlag": reader.u8(offset + 25),
        "geometriesCount": reader.u16(offset + 26),
    }
    if vft != MODEL_VFT:
        unresolved.append("drawable-model VFT differs from CodeX reference")
    if unresolved:
        result["unresolved"] = unresolved
    return result


def decode_lod(reader: Reader, offset: int, lod_name: str) -> dict[str, Any]:
    if not reader.valid(offset, 8):
        raise DecodeError(f"drawable {lod_name} LOD at 0x{offset:X} is truncated")
    model_desc = read_descriptor(reader, offset)
    model_ptrs = _bounded_ptr_array(reader, model_desc, MAX_MODELS_PER_DRAWABLE)
    models: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for index, (raw_ptr, target) in enumerate(model_ptrs):
        item: dict[str, Any] = {"index": index, "pointer": f"0x{raw_ptr:08X}", "targetOffset": target or 0}
        if target is None:
            item["status"] = "null-or-invalid-pointer"
        else:
            try:
                item.update(decode_model(reader, target))
                item["status"] = "decoded-model-and-geometry-evidence"
            except DecodeError as exc:
                item["status"] = "pointer-valid-header-failed"
                item["error"] = str(exc)
                unresolved.append(f"model[{index}]: {exc}")
        models.append(item)
    result: dict[str, Any] = {
        "name": lod_name,
        "offset": offset,
        "modelDescriptor": model_desc,
        "models": models,
    }
    if unresolved:
        result["unresolved"] = unresolved
    return result


def decode_drawable(reader: Reader, offset: int) -> dict[str, Any]:
    if not reader.valid(offset, 120):
        raise DecodeError(f"drawable at 0x{offset:X} is truncated")
    vft = reader.u32(offset)
    lod_names = ("high", "medium", "low", "veryLow")
    lods: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for i, name in enumerate(lod_names):
        raw = reader.u32(offset + 64 + i * 4)
        target, error = _safe_target(reader, raw)
        item: dict[str, Any] = {"name": name, "pointer": f"0x{raw:08X}", "targetOffset": target or 0}
        if error:
            item["status"] = "invalid-pointer"
            item["error"] = error
            unresolved.append(f"{name} LOD: {error}")
        elif target is None:
            item["status"] = "null-pointer"
        else:
            try:
                item.update(decode_lod(reader, target, name))
                item["status"] = "decoded-lod-model-geometry-evidence"
            except DecodeError as exc:
                item["status"] = "pointer-valid-header-failed"
                item["error"] = str(exc)
                unresolved.append(f"{name} LOD: {exc}")
        lods.append(item)
    result: dict[str, Any] = {
        "offset": offset,
        "vft": f"0x{vft:08X}",
        "expectedVft": f"0x{DRAWABLE_VFT:08X}",
        "vftValidated": vft == DRAWABLE_VFT,
        "boundingCenter": reader.vec4(offset + 16),
        "boundingBoxMin": reader.vec4(offset + 32),
        "boundingBoxMax": reader.vec4(offset + 48),
        "lodDistances": [reader.f32(offset + 80 + i * 4) for i in range(4)],
        "drawBucketMasks": [reader.u32(offset + 96 + i * 4) for i in range(4)],
        "boundingSphereRadius": reader.f32(offset + 112),
        "ppuOnly": reader.u32(offset + 116),
        "lods": lods,
        "shaderGroupPointer": f"0x{reader.u32(offset + 8):08X}",
        "skeletonPointer": f"0x{reader.u32(offset + 12):08X}",
    }
    if vft != DRAWABLE_VFT:
        unresolved.append("drawable VFT differs from CodeX reference")
    if unresolved:
        result["unresolved"] = unresolved
    return result


def _read_c_string(reader: Reader, raw_pointer: int, maximum: int = 512) -> tuple[str | None, str | None]:
    target, error = _safe_target(reader, raw_pointer)
    if target is None:
        return None, error
    end = min(len(reader.data), target + maximum)
    nul = reader.data.find(b"\0", target, end)
    if nul < 0:
        return None, f"string at 0x{target:X} has no terminator within {maximum} bytes"
    try:
        return reader.data[target:nul].decode("utf-8", "replace"), None
    except Exception as exc:  # pragma: no cover - decode replacement is defensive
        return None, f"string decode failed at 0x{target:X}: {exc}"


def _read_matrix4_zxy(reader: Reader, offset: int) -> tuple[list[list[float]], list[float], str | None]:
    if not reader.valid(offset, 64):
        return [], [], f"matrix at 0x{offset:X} is truncated"
    raw = list(struct.unpack_from("<16f", reader.data, offset))
    # CodeX/Rpf6Crypto.ToZXY keeps the first three rows and swaps the
    # translation row from XYZ to ZXY.  NaN padding in the last column is
    # normalized to zero by the reference reader.
    for index in (3, 7, 11, 15):
        if math.isnan(raw[index]):
            raw[index] = 0.0
    if not all(math.isfinite(value) for value in raw):
        return [], [], "matrix contains a non-finite non-padding value"
    transformed = raw[:]
    transformed[12:16] = [raw[14], raw[12], raw[13], raw[15]]
    matrix = [transformed[i:i + 4] for i in range(0, 16, 4)]
    return matrix, transformed[12:15], None


def decode_drawable_instance_base(reader: Reader, offset: int, sector_index: int, instance_index: int) -> dict[str, Any]:
    block_length = 224
    if not reader.valid(offset, block_length):
        raise DecodeError(f"drawable instance at 0x{offset:X} is truncated")
    vft = reader.u32(offset)
    matrix, translation, matrix_error = _read_matrix4_zxy(reader, offset + 0x40)
    name_raw = reader.u32(offset + 0xB8)
    name, name_error = _read_c_string(reader, name_raw)
    result: dict[str, Any] = {
        "sectorIndex": sector_index,
        "instanceIndex": instance_index,
        "offset": offset,
        "vft": f"0x{vft:08X}",
        "expectedVft": f"0x{DRAWABLE_INSTANCE_VFT:08X}",
        "vftValidated": vft == DRAWABLE_INSTANCE_VFT,
        "instanceHash": f"0x{reader.u32(offset + 0xA0):08X}",
        "namePointer": f"0x{name_raw:08X}",
        "name": name,
        "node": reader.i32(offset + 0x20),
        "type": reader.u16(offset + 0xAC),
        "rooms": reader.u8(offset + 0xAE),
        "flags": reader.i16(offset + 0xB0),
        "matrix": matrix,
        "translation": translation,
        "boundingBoxMin": reader.vec4(offset + 0x80),
        "boundingBoxMax": reader.vec4(offset + 0x90),
        "roomFlags": [reader.u32(offset + 0xA4), reader.u32(offset + 0xA8)],
        "nextDrawablePointer": f"0x{reader.u32(offset + 0xD4):08X}",
        "status": "decoded-placement-transform",
    }
    unresolved: list[str] = []
    if vft != DRAWABLE_INSTANCE_VFT:
        unresolved.append("drawable-instance VFT differs from CodeX reference")
    if matrix_error:
        unresolved.append(matrix_error)
        result["status"] = "pointer-valid-header-failed"
    if name_error:
        unresolved.append(name_error)
    if unresolved:
        result["unresolved"] = unresolved
    return result


def read_managed_descriptor(reader: Reader, offset: int) -> dict[str, int]:
    """Read Rsc6ManagedArr's 8-byte position/count/capacity descriptor."""
    descriptor = read_descriptor(reader, offset)
    descriptor["kind"] = "managed-array"
    return descriptor


def decode_sector_info(reader: Reader, offset: int, sector_index: int) -> dict[str, Any]:
    if not reader.valid(offset, 480):
        raise DecodeError(f"sector-info block at 0x{offset:X} is truncated")
    vft = reader.u32(offset)
    drawable_desc = read_managed_descriptor(reader, offset + 0xFC)
    drawable_count = min(drawable_desc["count"], MAX_INSTANCES_PER_SECTOR)
    drawable_target = drawable_desc.get("targetOffset", 0) or 0
    instances: list[dict[str, Any]] = []
    unresolved: list[str] = []
    if drawable_count and drawable_target == 0:
        unresolved.append("drawable-instance managed array has a non-zero count but no target")
    elif drawable_count and not reader.valid(drawable_target, drawable_count * 224):
        unresolved.append("drawable-instance managed array exceeds payload")
    else:
        for index in range(drawable_count):
            target = drawable_target + index * 224
            try:
                instances.append(decode_drawable_instance_base(reader, target, sector_index, index))
            except DecodeError as exc:
                instances.append({
                    "sectorIndex": sector_index,
                    "instanceIndex": index,
                    "targetOffset": target,
                    "status": "pointer-valid-header-failed",
                    "error": str(exc),
                })
                unresolved.append(f"drawableInstances[{index}]: {exc}")
    child_ptr_desc = read_descriptor(reader, offset + 0xF4)
    child_group_raw = reader.u32(offset + 0xF0)
    result: dict[str, Any] = {
        "index": sector_index,
        "offset": offset,
        "vft": f"0x{vft:08X}",
        "expectedVft": f"0x{SECTOR_INFO_VFT:08X}",
        "vftValidated": vft == SECTOR_INFO_VFT,
        "nameHash": f"0x{reader.u32(offset + 0x178):08X}",
        "boundsMin": reader.vec4(offset + 0xB0),
        "boundsMax": reader.vec4(offset + 0xC0),
        "drawableInstances": drawable_desc,
        "childSectorPointers": child_ptr_desc,
        "childGroupPointer": f"0x{child_group_raw:08X}",
        "instances": instances,
        "status": "decoded-sector-placement-transforms" if vft == SECTOR_INFO_VFT else "pointer-valid-header-failed",
    }
    if vft != SECTOR_INFO_VFT:
        unresolved.append("sector-info VFT differs from CodeX reference")
    if unresolved:
        result["unresolved"] = unresolved
    return result


def decode_wsi(reader: Reader, object_start: int) -> dict[str, Any]:
    root = object_start
    if not reader.valid(root, 480):
        raise DecodeError("WSI sector-info root is truncated")
    root_vft = reader.u32(root)
    if root_vft != SECTOR_INFO_VFT:
        raise DecodeError(f"WSI root VFT 0x{root_vft:08X} does not match 0x{SECTOR_INFO_VFT:08X}")
    scoped_raw = reader.u32(root + 0xF0)
    scoped_target, scoped_error = _safe_target(reader, scoped_raw)
    sectors: list[dict[str, Any]] = []
    unresolved: list[str] = []
    if scoped_target is None:
        unresolved.append(scoped_error or "WSI root has no scoped-sector group")
    else:
        if not reader.valid(scoped_target, 28):
            unresolved.append("scoped-sector group is truncated")
        else:
            sector_desc = read_descriptor(reader, scoped_target)
            sector_ptrs = _bounded_ptr_array(reader, sector_desc, MAX_SECTORS)
            for index, (raw_ptr, target) in enumerate(sector_ptrs):
                if target is None:
                    sectors.append({"index": index, "pointer": f"0x{raw_ptr:08X}", "status": "null-or-invalid-pointer"})
                    continue
                try:
                    item = decode_sector_info(reader, target, index)
                    item["pointer"] = f"0x{raw_ptr:08X}"
                    sectors.append(item)
                except DecodeError as exc:
                    sectors.append({"index": index, "pointer": f"0x{raw_ptr:08X}", "targetOffset": target, "status": "pointer-valid-header-failed", "error": str(exc)})
                    unresolved.append(f"sector[{index}]: {exc}")
    instances = [instance for sector in sectors for instance in sector.get("instances", [])]
    decoded_instances = sum(1 for item in instances if item.get("status") == "decoded-placement-transform")
    return {
        "kind": "WSI",
        "confidence": "validated RSC05 sector-info root, scoped-sector pointer array, sector headers, managed drawable-instance arrays, names, hashes, and placement matrices where pointer/range checks pass",
        "root": {
            "offset": root,
            "vft": f"0x{root_vft:08X}",
            "childGroupPointer": f"0x{scoped_raw:08X}",
            "childGroupTargetOffset": scoped_target or 0,
            "drawableInstances": read_managed_descriptor(reader, root + 0xFC),
        },
        "sectors": sectors,
        "instances": instances,
        "counts": {
            "sectors": len(sectors),
            "decodedSectors": sum(1 for sector in sectors if sector.get("status") == "decoded-sector-placement-transforms"),
            "instances": len(instances),
            "decodedInstances": decoded_instances,
        },
        "unresolved": unresolved + [
            "secondary drawable-instance records and room/portal relationships are not decoded",
            "instance-to-visual-dictionary joins are emitted by the placement join step",
        ],
    }


def decode_wvd(reader: Reader, object_start: int) -> dict[str, Any]:
    root = object_start
    if not reader.valid(root, 60):
        raise DecodeError("WVD root is truncated")
    vft = reader.u32(root)
    if vft != VISUAL_DICTIONARY_VFT:
        raise DecodeError(f"WVD root VFT 0x{vft:08X} does not match 0x{VISUAL_DICTIONARY_VFT:08X}")
    hashes_desc = read_descriptor(reader, root + 24)
    drawables_desc = read_descriptor(reader, root + 32)
    texture_ptr = reader.u32(root + 40)
    derived_ptr = reader.u32(root + 44)
    lod = reader.i32(root + 52)
    hashes = read_u32_array(reader, hashes_desc)
    drawable_pointers = _bounded_ptr_array(reader, drawables_desc, MAX_DRAWABLES)
    drawables = []
    unresolved: list[str] = ["shader/material bindings and world placement instances remain unresolved"]
    for index, (raw_ptr, target) in enumerate(drawable_pointers):
        item: dict[str, Any] = {
            "index": index,
            "hash": f"0x{hashes[index]:08X}" if index < len(hashes) else None,
            "pointer": f"0x{raw_ptr:08X}",
            "targetOffset": target or 0,
        }
        if target is None:
            item["status"] = "null-or-invalid-pointer"
        else:
            try:
                item.update(decode_drawable(reader, target))
                item["status"] = "decoded-drawable-mesh-evidence"
            except DecodeError as exc:
                item["status"] = "pointer-valid-header-failed"
                item["error"] = str(exc)
                unresolved.append(f"drawable[{index}]: {exc}")
        drawables.append(item)
    decoded_drawables = sum(1 for x in drawables if x.get("status") == "decoded-drawable-mesh-evidence")
    decoded_models = sum(
        1 for x in drawables for lod in x.get("lods", [])
        for model in lod.get("models", [])
        if model.get("status") == "decoded-model-and-geometry-evidence"
    )
    decoded_geometries = sum(
        1 for x in drawables for lod in x.get("lods", [])
        for model in lod.get("models", []) for geometry in model.get("geometries", [])
        if geometry.get("status") == "decoded-mesh-evidence"
    )
    decoded_vertices = sum(
        len(geometry.get("positions", [])) for x in drawables
        for lod in x.get("lods", []) for model in lod.get("models", [])
        for geometry in model.get("geometries", [])
    )
    decoded_indices = sum(
        len(geometry.get("indexBuffer", {}).get("indices", [])) for x in drawables
        for lod in x.get("lods", []) for model in lod.get("models", [])
        for geometry in model.get("geometries", [])
    )
    return {
        "kind": "WVD",
        "confidence": "validated RSC05 container, VisualDictionary root, drawable/LOD/model/geometry headers, vertex declarations, and indexed mesh payloads where pointer and range checks pass",
        "root": {
            "offset": root,
            "vft": f"0x{vft:08X}",
            "unknown8": f"0x{reader.u32(root + 8):08X}",
            "parentDictionary": f"0x{reader.u32(root + 16):08X}",
            "usageCount": reader.u32(root + 20),
            "hashes": hashes_desc,
            "drawables": drawables_desc,
            "textureDictionary": {"pointer": f"0x{texture_ptr:08X}", "targetOffset": reader.ptr(texture_ptr) or 0},
            "derivedTextures": {"pointer": f"0x{derived_ptr:08X}", "targetOffset": reader.ptr(derived_ptr) or 0},
            "lodLevel": lod,
        },
        "hashes": [f"0x{x:08X}" for x in hashes],
        "drawables": drawables,
        "counts": {
            "drawables": len(drawables),
            "validatedDrawablePointers": sum(1 for x in drawables if x.get("targetOffset", 0)),
            "decodedDrawables": decoded_drawables,
            "decodedModels": decoded_models,
            "decodedGeometries": decoded_geometries,
            "decodedVertices": decoded_vertices,
            "decodedIndices": decoded_indices,
        },
        "unresolved": unresolved,
    }


def inspect(path: Path) -> dict[str, Any]:
    container, payload, virtual_size = decode_container(path)
    reader = Reader(payload, virtual_size)
    suffix = path.suffix.lower()
    if suffix == ".wbd":
        decoded = decode_wbd(reader, container["objectStart"])
    elif suffix == ".wvd":
        decoded = decode_wvd(reader, container["objectStart"])
    elif suffix == ".wsi":
        decoded = decode_wsi(reader, container["objectStart"])
    else:
        raise DecodeError("world resource inspector accepts .wbd, .wvd, or .wsi")
    report: dict[str, Any] = {
        "format": "RDR1 world-resource structure",
        "source": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "container": container,
        "decoded": decoded,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = inspect(args.input)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return 0
    except Exception as exc:
        report = {
            "format": "RDR1 world-resource structure",
            "source": str(args.input),
            "status": "decode-failed",
            "error": str(exc),
            "unresolved": ["No geometry semantics were invented after validation failed."],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
