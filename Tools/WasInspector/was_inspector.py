#!/usr/bin/env python3
"""Read-only structural inspector for RDR1 PC/Switch RSC5 .was resources."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import pathlib
import re
import struct
import sys
from typing import Any


RSC5_MAGIC = b"RSC\x05"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
POINTER_BASE = 0x50000000
POINTER_MASK = 0x0FFFFFFF
STRING_RE = re.compile(rb"[A-Za-z0-9_./\\:$@+ -]{4,}\x00")
SYMBOLIC_NAME_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
ANIMATION_RECORD_VTABLE = 0x04A1F634
CHANNEL_PAYLOAD_CLASSES = {
    0x04A209E4: ("component_stream_0e", 0x30),
    0x04A22744: ("sampled_stream_06", 0x20),
    0x04A21E78: ("constant_quaternion", 0x10),
    0x04A21DEC: ("constant_float", 0x10),
    0x04A21F90: ("constant_vector3", 0x10),
}
TRACK_KIND_NAMES = {
    0: "vector_track",
    1: "quaternion_track",
    5: "root_vector_track",
    6: "root_quaternion_track",
    # Facial/auxiliary controls used by cinematic and mini-game clips.
    24: "scalar_track",
    25: "vector_track",
    26: "quaternion_track",
}


class InspectionError(RuntimeError):
    pass


def decode_rsc5_flags(flag: int) -> dict[str, Any]:
    virtual_shift = ((flag >> 11) & 0xF) + 8
    physical_shift = ((flag >> 26) & 0xF) + 8
    virtual_size = (flag & 0x7FF) << virtual_shift
    physical_size = ((flag >> 15) & 0x7FF) << physical_shift
    return {
        "raw": f"0x{flag:08X}",
        "is_resource": bool(flag & 0x80000000),
        "is_compressed": bool(flag & 0x40000000),
        "virtual_size": virtual_size,
        "physical_size": physical_size,
        "total_size": virtual_size + physical_size,
        "virtual_shift": virtual_shift,
        "physical_shift": physical_shift,
    }


def decompress_zstd(payload: bytes) -> bytes:
    try:
        import zstandard
    except ImportError:
        # The studio ships a small Python 3.12 dependency beside the
        # inspectors, while Blender 5.2 embeds Python 3.13.  If PYTHONPATH
        # exposes the bundled extension first, its ABI may not match the
        # interpreter even though Blender has its own compatible package.
        optional_roots = [
            entry for entry in sys.path
            if pathlib.Path(entry).name.casefold() == "_deps"
        ]
        for entry in optional_roots:
            while entry in sys.path:
                sys.path.remove(entry)
        # A failed package import can leave a partially initialized module in
        # sys.modules. Clear it before retrying the normal site-packages path.
        for module_name in list(sys.modules):
            if module_name == "zstandard" or module_name.startswith("zstandard."):
                sys.modules.pop(module_name, None)
        try:
            import zstandard
        except ImportError as fallback_error:
            raise InspectionError(
                "The 'zstandard' Python package is required to inspect compressed RSC5 files "
                "(the bundled extension is incompatible with this Python runtime)."
            ) from fallback_error
        finally:
            # Keep the optional dependency available for later imports without
            # changing the module that was successfully loaded above.
            for entry in reversed(optional_roots):
                sys.path.insert(0, entry)

    try:
        import io

        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(payload)) as reader:
            return reader.read()
    except zstandard.ZstdError as exc:
        raise InspectionError(f"Invalid Zstandard payload: {exc}") from exc


def load_rsc5(path: pathlib.Path) -> tuple[bytes, int, dict[str, Any], str, bytes]:
    raw = path.read_bytes()
    if len(raw) < 12:
        raise InspectionError("File is too small to contain an RSC5 header.")
    if raw[:4] != RSC5_MAGIC:
        raise InspectionError(f"Expected RSC5 magic, found {raw[:4]!r}.")
    resource_type, flag = struct.unpack_from("<II", raw, 4)
    flags = decode_rsc5_flags(flag)
    payload = raw[12:]
    if flags["is_compressed"]:
        if not payload.startswith(ZSTD_MAGIC):
            raise InspectionError("Compressed RSC5 payload does not start with Zstandard magic.")
        decoded = decompress_zstd(payload)
        compression = "zstandard"
    else:
        decoded = payload
        compression = "none"
    return raw, resource_type, flags, compression, decoded


def extract_strings(data: bytes) -> list[dict[str, Any]]:
    strings: list[dict[str, Any]] = []
    for match in STRING_RE.finditer(data):
        raw = match.group()[:-1]
        # Random compressed-looking blocks can accidentally contain short printable runs.
        # Keep strings that contain a letter and have a clean NUL terminator.
        if not any((65 <= byte <= 90) or (97 <= byte <= 122) for byte in raw):
            continue
        strings.append(
            {
                "offset": match.start(),
                "offset_hex": f"0x{match.start():X}",
                "value": raw.decode("ascii", "replace"),
            }
        )
    return strings


def read_ascii_cstring(data: bytes, offset: int, maximum: int = 512) -> str | None:
    if offset < 0 or offset >= len(data):
        return None
    end = data.find(b"\x00", offset, min(len(data), offset + maximum))
    if end <= offset:
        return None
    raw = data[offset:end]
    if any(byte < 32 or byte > 126 for byte in raw):
        return None
    return raw.decode("ascii")


def scan_animation_source_records(data: bytes) -> list[dict[str, Any]]:
    """Find the 0x30-byte animation records established from multiple gent.was entries."""
    records: list[dict[str, Any]] = []
    for offset in range(0, len(data) - 0x2F, 4):
        if struct.unpack_from("<I", data, offset)[0] != ANIMATION_RECORD_VTABLE:
            continue
        flags = struct.unpack_from("<I", data, offset + 4)[0]
        frame_count, frame_field_0a = struct.unpack_from("<HH", data, offset + 8)
        duration = struct.unpack_from("<f", data, offset + 0xC)[0]
        skeleton_signature = struct.unpack_from("<I", data, offset + 0x10)[0]
        track_pointer = struct.unpack_from("<I", data, offset + 0x14)[0]
        array_count, array_capacity = struct.unpack_from("<HH", data, offset + 0x18)
        path_pointer = struct.unpack_from("<I", data, offset + 0x1C)[0]
        reserved0, reserved1, data_size, format_version = struct.unpack_from(
            "<IIII", data, offset + 0x20
        )
        if (
            track_pointer >> 28 != 5
            or path_pointer >> 28 != 5
            or frame_count == 0
            or not math.isfinite(duration)
            or duration <= 0
            or array_count == 0
            or array_capacity < array_count
            or reserved0 != 0
            or reserved1 != 0
        ):
            continue
        track_offset = track_pointer & POINTER_MASK
        path_offset = path_pointer & POINTER_MASK
        if track_offset >= len(data) or path_offset >= len(data):
            continue
        source_path = read_ascii_cstring(data, path_offset)
        if not source_path or not source_path.lower().endswith(".anim"):
            continue
        inferred_fps = (frame_count - 1) / duration
        records.append(
            {
                "offset": offset,
                "offset_hex": f"0x{offset:X}",
                "name": pathlib.PureWindowsPath(source_path).stem,
                "source_path": source_path,
                "source_path_offset": path_offset,
                "source_path_offset_hex": f"0x{path_offset:X}",
                "frame_count": frame_count,
                "frame_field_0a": frame_field_0a,
                "duration_seconds": round(duration, 6),
                "inferred_fps": round(inferred_fps, 3),
                "flags": f"0x{flags:08X}",
                "skeleton_signature": f"0x{skeleton_signature:08X}",
                "track_data_offset": track_offset,
                "track_data_offset_hex": f"0x{track_offset:X}",
                "track_array_count": array_count,
                "track_array_capacity": array_capacity,
                "encoded_data_size": data_size,
                "format_version": format_version,
            }
        )
    return records


def scan_padded_pointer_table(data: bytes, offset: int, maximum: int = 256) -> list[int]:
    """Read RSC5 pointers stored in 16-byte, 0xCD-padded slots."""
    targets: list[int] = []
    for index in range(maximum):
        position = offset + index * 16
        if position + 16 > len(data):
            break
        pointer = struct.unpack_from("<I", data, position)[0]
        if pointer >> 28 != 5 or data[position + 4:position + 16] != b"\xCD" * 12:
            break
        target = pointer & POINTER_MASK
        if target >= len(data):
            break
        targets.append(target)
    return targets


def scan_pointer_array(data: bytes, offset: int, count: int) -> list[int]:
    """Read a compact RSC5 pointer array used by some multi-segment clips.

    Most animation records use 16-byte slots (one pointer followed by twelve
    0xCD bytes), but a few records store the sequence pointers contiguously
    and describe the count in the record header.  The game accepts both
    layouts; keeping this fallback here lets the offline decoder do the same.
    """
    if count <= 0 or count > 256 or offset < 0 or offset + count * 4 > len(data):
        return []
    targets: list[int] = []
    for index in range(count):
        pointer = struct.unpack_from("<I", data, offset + index * 4)[0]
        target = pointer & POINTER_MASK
        if pointer >> 28 != 5 or target >= len(data):
            return []
        targets.append(target)
    return targets


def parse_compressed_sequence(data: bytes, offset: int) -> dict[str, Any] | None:
    if offset + 0x18 > len(data):
        return None
    self_pointer, channels_pointer = struct.unpack_from("<II", data, offset)
    if self_pointer != POINTER_BASE | offset or channels_pointer >> 28 != 5:
        return None
    channel_count, channel_capacity = struct.unpack_from("<HH", data, offset + 8)
    encoded_size = struct.unpack_from("<I", data, offset + 0xC)[0]
    frame_count, sequence_type = struct.unpack_from("<HH", data, offset + 0x10)
    reserved = struct.unpack_from("<I", data, offset + 0x14)[0]
    channels_offset = channels_pointer & POINTER_MASK
    if (
        channel_count == 0
        or channel_capacity < channel_count
        or channel_count > 1024
        or channels_offset + channel_count * 4 > len(data)
        or reserved != 0
    ):
        return None

    descriptors: list[dict[str, Any]] = []
    for index in range(channel_count):
        descriptor_pointer = struct.unpack_from("<I", data, channels_offset + index * 4)[0]
        descriptor_offset = descriptor_pointer & POINTER_MASK
        if descriptor_pointer >> 28 != 5 or descriptor_offset + 4 > len(data):
            return None
        if descriptor_offset + 0x18 > len(data):
            return None
        kind, encoded_type, bone_id = struct.unpack_from("<BBH", data, descriptor_offset)
        component_pointers = struct.unpack_from("<IIII", data, descriptor_offset + 4)
        component_count = struct.unpack_from("<I", data, descriptor_offset + 0x14)[0]
        if component_count == 0 or component_count > 4:
            return None
        component_offsets: list[int] = []
        for component_index, pointer in enumerate(component_pointers):
            if component_index < component_count:
                component_offset = pointer & POINTER_MASK
                if pointer >> 28 != 5 or component_offset >= len(data):
                    return None
                component_offsets.append(component_offset)
            elif pointer != 0:
                return None
        descriptors.append(
            {
                "offset": descriptor_offset,
                "kind": kind,
                # Facial/auxiliary animation uses additional track ids.  The
                # payload shape is authoritative for decoding: one component
                # is a scalar (blend/face control), three components are a
                # vector control.  Keep the original numeric id as well.
                "kind_name": TRACK_KIND_NAMES.get(
                    kind, "scalar_track" if component_count == 1 else
                    "unknown_vector_track" if component_count == 3 else "unknown"
                ),
                "encoded_type": encoded_type,
                "type_low_nibble": encoded_type & 0x0F,
                "bone_id": bone_id,
                "component_count": component_count,
                "component_offsets": component_offsets,
            }
        )

    return {
        "offset": offset,
        "offset_hex": f"0x{offset:X}",
        "channels_offset": channels_offset,
        "channels_offset_hex": f"0x{channels_offset:X}",
        "channel_count": channel_count,
        "channel_capacity": channel_capacity,
        "encoded_size": encoded_size,
        "frame_count": frame_count,
        "sequence_type": sequence_type,
        "descriptors": descriptors,
    }


def parse_channel_payload(data: bytes, offset: int) -> dict[str, Any] | None:
    if offset + 8 > len(data):
        return None
    signature, flags = struct.unpack_from("<II", data, offset)
    class_info = CHANNEL_PAYLOAD_CLASSES.get(signature)
    if class_info is None:
        return None
    class_name, size = class_info
    if offset + size > len(data):
        return None
    result: dict[str, Any] = {
        "offset": offset,
        "offset_hex": f"0x{offset:X}",
        "signature": f"0x{signature:08X}",
        "class": class_name,
        "flags": f"0x{flags:08X}",
    }

    if signature == 0x04A21DEC:
        if flags != 0x400:
            return None
        result["value"] = struct.unpack_from("<f", data, offset + 8)[0]
    elif signature in (0x04A21E78, 0x04A21F90):
        expected_flags = 0x900 if signature == 0x04A21E78 else 0xD00
        pointer = struct.unpack_from("<I", data, offset + 8)[0]
        target = pointer & POINTER_MASK
        if flags != expected_flags or (pointer != 0 and (pointer >> 28 != 5 or target >= len(data))):
            return None
        result["data_offset"] = target if pointer else None
        result["data_offset_hex"] = f"0x{target:X}" if pointer else None
    elif signature == 0x04A22744:
        pointer, field_0c, field_10 = struct.unpack_from("<III", data, offset + 8)
        target = pointer & POINTER_MASK
        if flags != 0x600 or (pointer != 0 and (pointer >> 28 != 5 or target >= len(data))):
            return None
        result.update(
            {
                "data_offset": target if pointer else None,
                "data_offset_hex": f"0x{target:X}" if pointer else None,
                "field_0c": field_0c,
                "field_10": field_10,
                "scale": struct.unpack_from("<f", data, offset + 0x14)[0],
                "offset_value": struct.unpack_from("<f", data, offset + 0x18)[0],
            }
        )
    elif signature == 0x04A209E4:
        component_count = struct.unpack_from("<I", data, offset + 0x20)[0]
        if flags != 0xE00 or component_count > 3:
            return None
        components: list[dict[str, Any]] = []
        for component_index in range(3):
            base = offset + 8 + component_index * 8
            value, pointer = struct.unpack_from("<fI", data, base)
            target = pointer & POINTER_MASK
            if pointer != 0 and (pointer >> 28 != 5 or target >= len(data)):
                return None
            components.append(
                {
                    "value": value,
                    "data_offset": target if pointer else None,
                    "data_offset_hex": f"0x{target:X}" if pointer else None,
                    "active": component_index < component_count,
                }
            )
        result["component_count"] = component_count
        result["components"] = components
    return result


def unpack_quantized_samples(
    data: bytes,
    offset: int,
    bit_width: int,
    sample_count: int,
    scale: float,
    base: float,
) -> list[float]:
    if bit_width <= 0 or bit_width > 32 or sample_count < 0 or sample_count > 1_000_000:
        raise InspectionError("Invalid quantized sample dimensions.")
    byte_count = (bit_width * sample_count + 7) // 8
    if offset < 0 or offset + byte_count > len(data):
        raise InspectionError("Quantized sample data extends beyond the resource.")
    packed = int.from_bytes(data[offset:offset + byte_count], "little")
    mask = (1 << bit_width) - 1
    return [base + ((packed >> (index * bit_width)) & mask) * scale for index in range(sample_count)]


def decode_payload_samples(data: bytes, offset: int) -> dict[str, Any]:
    payload = parse_channel_payload(data, offset)
    if payload is None:
        return {"status": "invalid", "offset_hex": f"0x{offset:X}"}
    result = {
        "status": "metadata_only",
        "class": payload["class"],
        "offset_hex": payload["offset_hex"],
    }
    if payload["class"] == "constant_float":
        result.update({"status": "decoded", "sample_count": 1, "values": [payload["value"]]})
    elif payload["class"] == "sampled_stream_06" and payload["data_offset"] is not None:
        values = unpack_quantized_samples(
            data,
            payload["data_offset"],
            payload["field_0c"],
            payload["field_10"],
            payload["scale"],
            payload["offset_value"],
        )
        result.update(
            {
                "status": "decoded",
                "sample_count": len(values),
                "bit_width": payload["field_0c"],
                "scale": payload["scale"],
                "base": payload["offset_value"],
                "values": values,
            }
        )
    elif payload["class"] == "component_stream_0e":
        axes: list[dict[str, Any]] = []
        for component in payload["components"]:
            axis: dict[str, Any] = {"base": component["value"]}
            header_offset = component["data_offset"]
            if header_offset is None:
                axis.update({"status": "constant", "sample_count": 1, "values": [component["value"]]})
            elif header_offset + 0x10 <= len(data):
                scale, pointer, bit_width, sample_count = struct.unpack_from(
                    "<fIII", data, header_offset
                )
                sample_offset = pointer & POINTER_MASK
                if pointer >> 28 != 5 or sample_offset >= len(data):
                    axis["status"] = "invalid"
                else:
                    values = unpack_quantized_samples(
                        data, sample_offset, bit_width, sample_count, scale, component["value"]
                    )
                    axis.update(
                        {
                            "status": "decoded",
                            "sample_count": len(values),
                            "bit_width": bit_width,
                            "scale": scale,
                            "values": values,
                        }
                    )
            else:
                axis["status"] = "invalid"
            axes.append(axis)
        result.update({"status": "decoded", "axes": axes})
    elif payload["class"] == "constant_quaternion" and payload.get("data_offset") is not None:
        if payload["data_offset"] + 16 > len(data):
            raise InspectionError("Constant quaternion extends beyond the resource.")
        values = list(struct.unpack_from("<ffff", data, payload["data_offset"]))
        if not all(math.isfinite(value) for value in values):
            raise InspectionError("Constant quaternion contains a non-finite component.")
        result.update({"status": "decoded", "sample_count": 1, "values": values})
    elif payload["class"] == "constant_vector3" and payload.get("data_offset") is not None:
        if payload["data_offset"] + 16 > len(data):
            raise InspectionError("Constant vector extends beyond the resource.")
        values = list(struct.unpack_from("<fff", data, payload["data_offset"]))
        if not all(math.isfinite(value) for value in values):
            raise InspectionError("Constant vector contains a non-finite component.")
        sentinel = struct.unpack_from("<I", data, payload["data_offset"] + 12)[0]
        result.update(
            {
                "status": "decoded",
                "sample_count": 1,
                "values": values,
                "sentinel_hex": f"0x{sentinel:08X}",
            }
        )
    elif payload.get("data_offset") is not None:
        result["data_offset_hex"] = payload["data_offset_hex"]
    return result


def analyze_track_graph(data: bytes, records: list[dict[str, Any]]) -> dict[str, Any]:
    sequence_references = 0
    static_references = 0
    validation_errors = 0
    unique_sequences: dict[int, dict[str, Any]] = {}
    static_targets: dict[int, int] = {}

    for record in records:
        targets = scan_padded_pointer_table(data, record["track_data_offset"])
        record_sequences: list[dict[str, Any]] = []
        record_static: list[dict[str, Any]] = []
        for target in targets:
            sequence = parse_compressed_sequence(data, target)
            if sequence is not None:
                sequence_references += 1
                unique_sequences.setdefault(target, sequence)
                record_sequences.append(sequence)
                continue
            if target + 4 <= len(data):
                class_signature = struct.unpack_from("<I", data, target)[0]
                static_references += 1
                static_targets[target] = class_signature
                record_static.append(
                    {"offset_hex": f"0x{target:X}", "class_signature": f"0x{class_signature:08X}"}
                )
            else:
                validation_errors += 1

        bone_ids = sorted(
            {
                descriptor["bone_id"]
                for sequence in record_sequences
                for descriptor in sequence["descriptors"]
            }
        )
        record["track_graph"] = {
            "entry_count": len(targets),
            "compressed_sequence_count": len(record_sequences),
            "static_or_special_object_count": len(record_static),
            "channel_descriptor_count": sum(
                sequence["channel_count"] for sequence in record_sequences
            ),
            "unique_bone_ids": [f"0x{bone_id:04X}" for bone_id in bone_ids],
            "sequence_offsets": [sequence["offset_hex"] for sequence in record_sequences],
            "static_or_special_objects": record_static,
        }

    channel_count_distribution: collections.Counter[int] = collections.Counter()
    sequence_type_distribution: collections.Counter[int] = collections.Counter()
    descriptor_layouts: collections.Counter[tuple[int, int]] = collections.Counter()
    encoded_types: collections.Counter[int] = collections.Counter()
    bone_counts: collections.Counter[int] = collections.Counter()
    descriptor_offsets: set[int] = set()
    payload_reference_counts: collections.Counter[str] = collections.Counter()
    payloads_by_track_kind: collections.Counter[tuple[int, str]] = collections.Counter()
    unique_payloads: dict[int, dict[str, Any]] = {}
    invalid_payloads = 0
    for sequence in unique_sequences.values():
        channel_count_distribution[sequence["channel_count"]] += 1
        sequence_type_distribution[sequence["sequence_type"]] += 1
        for descriptor in sequence["descriptors"]:
            descriptor_layouts[(descriptor["kind"], descriptor["type_low_nibble"])] += 1
            encoded_types[descriptor["encoded_type"]] += 1
            bone_counts[descriptor["bone_id"]] += 1
            descriptor_offsets.add(descriptor["offset"])
            for payload_offset in descriptor["component_offsets"]:
                payload = parse_channel_payload(data, payload_offset)
                if payload is None:
                    invalid_payloads += 1
                    continue
                payload_reference_counts[payload["class"]] += 1
                payloads_by_track_kind[(descriptor["kind"], payload["class"])] += 1
                unique_payloads.setdefault(payload_offset, payload)

    unique_payload_counts = collections.Counter(
        payload["class"] for payload in unique_payloads.values()
    )

    static_classes = collections.Counter(static_targets.values())
    return {
        "sequence_entry_references": sequence_references + static_references,
        "compressed_sequence_references": sequence_references,
        "unique_compressed_sequences": len(unique_sequences),
        "static_or_special_object_references": static_references,
        "unique_static_or_special_objects": len(static_targets),
        "static_or_special_class_signatures": [
            {"signature": f"0x{signature:08X}", "count": count}
            for signature, count in static_classes.most_common()
        ],
        "unique_channel_descriptors": len(descriptor_offsets),
        "channel_payloads": {
            "references": sum(payload_reference_counts.values()),
            "unique": len(unique_payloads),
            "validation_errors": invalid_payloads,
            "classes": [
                {
                    "class": class_name,
                    "references": references,
                    "unique": unique_payload_counts[class_name],
                }
                for class_name, references in payload_reference_counts.most_common()
            ],
            "classes_by_track_kind": [
                {
                    "kind": kind,
                    "kind_name": TRACK_KIND_NAMES.get(kind, "unknown"),
                    "class": class_name,
                    "references": references,
                }
                for (kind, class_name), references in payloads_by_track_kind.most_common()
            ],
        },
        "channel_count_distribution": [
            {"channel_count": count, "sequences": occurrences}
            for count, occurrences in sorted(channel_count_distribution.items())
        ],
        "sequence_type_distribution": [
            {"type": value, "sequences": count}
            for value, count in sequence_type_distribution.most_common()
        ],
        "descriptor_layout_distribution": [
            {
                "kind": kind,
                "kind_name": TRACK_KIND_NAMES.get(kind, "unknown"),
                "type_low_nibble": track_type,
                "descriptors": count,
            }
            for (kind, track_type), count in descriptor_layouts.most_common()
        ],
        "encoded_type_distribution": [
            {"encoded_type": value, "encoded_type_hex": f"0x{value:02X}", "descriptors": count}
            for value, count in encoded_types.most_common()
        ],
        "bone_ids": {
            "count": len(bone_counts),
            "items": [
                {"bone_id": bone_id, "bone_id_hex": f"0x{bone_id:04X}", "sequences": count}
                for bone_id, count in bone_counts.most_common()
            ],
        },
        "validation_errors": validation_errors,
    }


def scan_pointers(data: bytes) -> tuple[list[dict[str, Any]], collections.Counter[int]]:
    pointers: list[dict[str, Any]] = []
    target_counts: collections.Counter[int] = collections.Counter()
    for source in range(0, len(data) - 3, 4):
        value = struct.unpack_from("<I", data, source)[0]
        if value >> 28 != 5:
            continue
        target = value & POINTER_MASK
        if target >= len(data):
            continue
        pointers.append(
            {
                "source": source,
                "source_hex": f"0x{source:X}",
                "value": f"0x{value:08X}",
                "target": target,
                "target_hex": f"0x{target:X}",
            }
        )
        target_counts[target] += 1
    return pointers, target_counts


def scan_array_headers(data: bytes) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for source in range(0, len(data) - 7, 4):
        pointer, count, capacity = struct.unpack_from("<IHH", data, source)
        if pointer >> 28 != 5:
            continue
        target = pointer & POINTER_MASK
        if target >= len(data) or count == 0 or capacity < count or capacity > 0x4000:
            continue
        candidates.append(
            {
                "source": source,
                "source_hex": f"0x{source:X}",
                "target": target,
                "target_hex": f"0x{target:X}",
                "count": count,
                "capacity": capacity,
            }
        )
    return candidates


def byte_statistics(data: bytes) -> dict[str, Any]:
    counts = collections.Counter(data)
    entropy = -sum(
        (count / len(data)) * math.log2(count / len(data)) for count in counts.values()
    ) if data else 0.0
    return {
        "entropy_bits_per_byte": round(entropy, 4),
        "zero_bytes": counts[0],
        "zero_ratio": round(counts[0] / len(data), 6) if data else 0,
        "cd_padding_bytes": counts[0xCD],
        "cd_padding_ratio": round(counts[0xCD] / len(data), 6) if data else 0,
    }


def limited(items: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    shown = items if limit == 0 else items[:limit]
    return {
        "count": len(items),
        "items_shown": len(shown),
        "items_truncated": len(items) - len(shown),
        "items": shown,
    }


def inspect(path: pathlib.Path, details_limit: int = 1000) -> dict[str, Any]:
    raw, resource_type, flags, compression, decoded = load_rsc5(path)

    pointers, target_counts = scan_pointers(decoded)
    strings = extract_strings(decoded)
    arrays = scan_array_headers(decoded)
    animation_records = scan_animation_source_records(decoded)
    track_graph = analyze_track_graph(decoded, animation_records)
    internal_names = [
        item for item in strings if item["value"].lower().endswith((".ast", ".was"))
    ]
    clip_names = [item for item in strings if item["value"].lower().endswith(".anim")]
    symbolic_names = [item for item in strings if SYMBOLIC_NAME_RE.fullmatch(item["value"])]
    suffix_counts = collections.Counter(
        pathlib.PurePosixPath(item["value"].replace("\\", "/")).suffix.lower()
        for item in strings
    )
    fps_counts = collections.Counter(record["inferred_fps"] for record in animation_records)
    skeleton_counts = collections.Counter(
        record["skeleton_signature"] for record in animation_records
    )
    expected = flags["total_size"]
    result: dict[str, Any] = {
        "schema": "rdr1-animation-studio.was-inspector.v5",
        "source": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "file_size": len(raw),
        "header": {
            "magic": "RSC5",
            "resource_type": resource_type,
            "resource_type_hex": f"0x{resource_type:X}",
            "flags": flags,
            "compression": compression,
        },
        "decoded": {
            "size": len(decoded),
            "size_matches_flags": len(decoded) == expected,
            "sha256": hashlib.sha256(decoded).hexdigest(),
            "statistics": byte_statistics(decoded),
        },
        "animation": {
            "internal_resource_names": internal_names[:100],
            "clip_names": limited(clip_names, details_limit),
            "symbolic_event_names": limited(symbolic_names, details_limit),
            "source_records": limited(animation_records, details_limit),
            "track_graph": track_graph,
            "inferred_fps_distribution": [
                {"fps": fps, "count": count} for fps, count in fps_counts.most_common()
            ],
            "skeleton_signatures": [
                {"signature": signature, "count": count}
                for signature, count in skeleton_counts.most_common()
            ],
            "string_suffixes": [
                {"suffix": suffix or "(none)", "count": count}
                for suffix, count in suffix_counts.most_common(50)
            ],
        },
        "strings": limited(strings, details_limit),
        "virtual_pointers": {
            "count": len(pointers),
            "items_shown": len(pointers) if details_limit == 0 else min(len(pointers), details_limit),
            "items_truncated": 0 if details_limit == 0 else max(0, len(pointers) - details_limit),
            "unique_targets": len(target_counts),
            "most_referenced_targets": [
                {"target": target, "target_hex": f"0x{target:X}", "references": count}
                for target, count in target_counts.most_common(100)
            ],
            "items": pointers if details_limit == 0 else pointers[:details_limit],
        },
        "candidate_array_headers": limited(arrays, details_limit),
        "root_words": [
            {
                "offset": offset,
                "offset_hex": f"0x{offset:X}",
                "value": f"0x{struct.unpack_from('<I', decoded, offset)[0]:08X}",
            }
            for offset in range(0, min(len(decoded), 0x100), 4)
        ],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=pathlib.Path, help="Extracted .was file")
    parser.add_argument("-o", "--output", type=pathlib.Path, help="JSON report path")
    parser.add_argument(
        "--details-limit",
        type=int,
        default=1000,
        help="Maximum entries per detail list; use 0 for all (default: 1000)",
    )
    args = parser.parse_args()

    if args.details_limit < 0:
        parser.error("--details-limit must be zero or greater")

    try:
        report = inspect(args.input, args.details_limit)
    except (OSError, InspectionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    output = args.output or args.input.with_suffix(args.input.suffix + ".json")
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {output}")
    print(
        f"RSC5 type={report['header']['resource_type']} "
        f"decoded={report['decoded']['size']} "
        f"strings={report['strings']['count']} "
        f"animation_records={report['animation']['source_records']['count']} "
        f"pointers={report['virtual_pointers']['count']} "
        f"arrays={report['candidate_array_headers']['count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
