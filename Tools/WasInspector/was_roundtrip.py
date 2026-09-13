"""Template-preserving RSC5 WAS quantized-stream encoder and round-trip verifier.

Unknown objects remain opaque. This is not a general animation/Blender importer.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import struct
import sys

import was_inspector as wi
import zstandard


def unpack_codes(data: bytes, offset: int, width: int, count: int) -> list[int]:
    if not 1 <= width <= 32 or not 0 < count <= 1_000_000:
        raise wi.InspectionError("Invalid stream dimensions")
    length = (width * count + 7) // 8
    if offset < 0 or offset + length > len(data):
        raise wi.InspectionError("Stream extends outside resource")
    bits = int.from_bytes(data[offset:offset + length], "little")
    mask = (1 << width) - 1
    return [(bits >> (i * width)) & mask for i in range(count)]


def pack_codes(codes: list[int], width: int, original: bytes) -> bytes:
    if not 1 <= width <= 32 or not codes or len(codes) > 1_000_000:
        raise wi.InspectionError("Invalid stream dimensions")
    bit_count = width * len(codes)
    if len(original) != (bit_count + 7) // 8:
        raise wi.InspectionError("Stream size mismatch")
    mask = (1 << width) - 1
    packed = int.from_bytes(original, "little") & ~((1 << bit_count) - 1)
    for i, code in enumerate(codes):
        if type(code) is not int or not 0 <= code <= mask:
            raise wi.InspectionError("Quantized code outside bit-width range")
        packed |= code << (i * width)
    return packed.to_bytes(len(original), "little")


def collect_streams(data: bytes):
    records = wi.scan_animation_source_records(data)
    if not records:
        raise wi.InspectionError("No supported animation records found")
    streams = {}
    payloads = set()
    sequences = set()
    classes = collections.Counter()
    opaque = set()
    unrecognized_records = []

    def add(offset, width, count):
        key = (offset, width, count)
        streams[key] = unpack_codes(data, offset, width, count)

    for record in records:
        targets = wi.scan_padded_pointer_table(data, record["track_data_offset"])
        if not targets:
            unrecognized_records.append(record["name"])
        for target in targets:
            sequence = wi.parse_compressed_sequence(data, target)
            if sequence is None:
                opaque.add(target)
                continue
            sequences.add(target)
            for descriptor in sequence["descriptors"]:
                for offset in descriptor["component_offsets"]:
                    if offset in payloads:
                        continue
                    payloads.add(offset)
                    p = wi.parse_channel_payload(data, offset)
                    if p is None:
                        raise wi.InspectionError(f"Unsupported payload at 0x{offset:X}")
                    classes[p["class"]] += 1
                    if p["class"] == "sampled_stream_06":
                        if p["data_offset"] is None:
                            raise wi.InspectionError("Missing sampled stream pointer")
                        add(p["data_offset"], p["field_0c"], p["field_10"])
                    elif p["class"] == "component_stream_0e":
                        for component in p["components"]:
                            header = component["data_offset"]
                            if header is not None:
                                if header + 16 > len(data):
                                    raise wi.InspectionError("Truncated component header")
                                _, pointer, width, count = struct.unpack_from("<fIII", data, header)
                                if pointer >> 28 != 5:
                                    raise wi.InspectionError("Invalid component pointer")
                                add(pointer & wi.POINTER_MASK, width, count)
    return streams, {
        "animations": len(records), "sequences": len(sequences),
        "payload_classes": dict(classes), "opaque_objects_preserved": len(opaque),
        "quantized_streams": len(streams),
        "quantized_samples": sum(len(codes) for codes in streams.values()),
        "unrecognized_records_preserved": unrecognized_records,
        "discovery": "Existing padded-pointer scanner; coverage is not a complete format specification",
    }


def rebuild(source: pathlib.Path, output: pathlib.Path) -> dict:
    if source.resolve() == output.resolve():
        raise wi.InspectionError("Choose a new output file; source replacement is disabled")
    if output.exists():
        raise wi.InspectionError("Output already exists; choose a new filename")
    raw, resource_type, flags, compression, data = wi.load_rsc5(source)
    if resource_type != 6 or len(data) != flags["total_size"]:
        raise wi.InspectionError("Expected a type-6 WAS with valid resource size")
    streams, report = collect_streams(data)
    rebuilt = bytearray(data)
    for (offset, width, count), codes in streams.items():
        length = (width * count + 7) // 8
        rebuilt[offset:offset + length] = pack_codes(codes, width, data[offset:offset + length])
    if bytes(rebuilt) != data:
        raise wi.InspectionError("Round-trip changed decoded resource bytes")
    payload = zstandard.ZstdCompressor(level=9).compress(rebuilt) if compression == "zstandard" else bytes(rebuilt)
    candidate = raw[:12] + payload
    verified = wi.decompress_zstd(candidate[12:]) if compression == "zstandard" else candidate[12:]
    if verified != data:
        raise wi.InspectionError("Recompressed output failed decoded-byte verification")
    report.update({
        "schema": "rdr1-animation-studio.was-roundtrip.v1",
        "source": str(source.resolve()), "output": str(output.resolve()),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "output_sha256": hashlib.sha256(candidate).hexdigest(),
        "decoded_sha256": hashlib.sha256(data).hexdigest(),
        "decoded_bytes_identical": True, "header_identical": candidate[:12] == raw[:12],
        "decoded_size": len(data), "output_size": len(candidate),
        "in_game_tested": False,
        "scope": "Quantized integer streams re-encoded in original layout; other bytes preserved. No Blender import or relocation.",
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as file:
        file.write(candidate)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=pathlib.Path)
    parser.add_argument("-o", "--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    report_path = args.output.with_suffix(args.output.suffix + ".roundtrip.json")
    try:
        if report_path.exists() or report_path.resolve() == args.input.resolve():
            raise wi.InspectionError("Report path already exists or conflicts with source")
        report = rebuild(args.input, args.output)
        with report_path.open("x", encoding="utf-8") as file:
            json.dump(report, file, indent=2)
        print(json.dumps(report, indent=2))
    except (OSError, wi.InspectionError, ValueError, struct.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
