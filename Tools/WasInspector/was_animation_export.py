#!/usr/bin/env python3
"""Export decoded channel samples for one animation inside an RDR1 RSC5 .was file."""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import struct
import sys
from typing import Any

import was_inspector


def trim_values(item: dict[str, Any], limit: int) -> None:
    values = item.get("values")
    if isinstance(values, list):
        shown = values if limit == 0 else values[:limit]
        item["values"] = [round(value, 9) for value in shown]
        item["values_shown"] = len(shown)
        item["values_truncated"] = len(values) - len(shown)
    for axis in item.get("axes", []):
        trim_values(axis, limit)


def expand_scalar(values: list[float], frame_count: int) -> list[float]:
    if len(values) == 1:
        return values * frame_count
    if len(values) != frame_count:
        raise was_inspector.InspectionError(
            f"Curve has {len(values)} values but sequence has {frame_count} frames."
        )
    return values


def assemble_track_samples(
    track_kind_name: str, payloads: list[dict[str, Any]], frame_count: int
) -> dict[str, Any]:
    quaternion = track_kind_name in ("quaternion_track", "root_quaternion_track")
    vector = track_kind_name in ("vector_track", "root_vector_track", "unknown_vector_track")
    scalar = track_kind_name == "scalar_track"
    samples: list[list[float]]

    if quaternion and len(payloads) == 1 and payloads[0]["class"] == "constant_quaternion":
        samples = [payloads[0]["values"]] * frame_count
    elif quaternion and len(payloads) == 1 and payloads[0]["class"] == "component_stream_0e":
        axes = [expand_scalar(axis["values"], frame_count) for axis in payloads[0]["axes"]]
        samples = []
        for frame in range(frame_count):
            xyz = [axis[frame] for axis in axes]
            remainder = 1.0 - sum(value * value for value in xyz)
            if remainder < -1e-5:
                raise was_inspector.InspectionError(
                    "Packed quaternion components exceed unit length."
                )
            samples.append(xyz + [math.sqrt(max(0.0, remainder))])
    elif quaternion and len(payloads) == 4:
        curves = [expand_scalar(payload["values"], frame_count) for payload in payloads]
        samples = [[curve[frame] for curve in curves] for frame in range(frame_count)]
    elif vector and len(payloads) == 1 and payloads[0]["class"] == "constant_vector3":
        samples = [payloads[0]["values"]] * frame_count
    elif vector and len(payloads) == 3:
        curves = [expand_scalar(payload["values"], frame_count) for payload in payloads]
        samples = [[curve[frame] for curve in curves] for frame in range(frame_count)]
    elif scalar and len(payloads) == 1:
        samples = [[value] for value in expand_scalar(payloads[0]["values"], frame_count)]
    else:
        return {"status": "unsupported_layout"}

    pre_normalization_norms: list[float] = []
    if quaternion:
        normalized: list[list[float]] = []
        for sample in samples:
            norm = math.sqrt(sum(value * value for value in sample))
            if not math.isfinite(norm) or norm <= 1e-12:
                raise was_inspector.InspectionError("Quaternion sample cannot be normalized.")
            pre_normalization_norms.append(norm)
            normalized.append([value / norm for value in sample])
        samples = normalized

    result: dict[str, Any] = {
        "status": "decoded",
        "format": "xyzw" if quaternion else "xyz" if vector else "scalar",
        "sample_count": len(samples),
        "samples": samples,
    }
    if quaternion:
        result.update(
            {
                "normalized": True,
                "pre_normalization_norm_min": min(pre_normalization_norms),
                "pre_normalization_norm_max": max(pre_normalization_norms),
            }
        )
    return result


def trim_track_samples(item: dict[str, Any], limit: int) -> None:
    samples = item.get("samples")
    if not isinstance(samples, list):
        return
    shown = samples if limit == 0 else samples[:limit]
    item["samples"] = [[round(value, 9) for value in sample] for sample in shown]
    item["samples_shown"] = len(shown)
    item["samples_truncated"] = len(samples) - len(shown)


def select_record(records: list[dict[str, Any]], query: str) -> dict[str, Any]:
    lowered = query.casefold()
    exact = [record for record in records if record["name"].casefold() == lowered]
    if len(exact) == 1:
        return exact[0]
    partial = [record for record in records if lowered in record["name"].casefold()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise was_inspector.InspectionError(f"No animation name contains {query!r}.")
    examples = ", ".join(record["name"] for record in partial[:8])
    raise was_inspector.InspectionError(
        f"Animation query is ambiguous ({len(partial)} matches): {examples}"
    )


def export_animation(path: pathlib.Path, query: str, sample_limit: int = 120,
                     decoded_data: bytes | None = None,
                     records: list[dict[str, Any]] | None = None,
                     record_offset: int | None = None) -> dict[str, Any]:
    if decoded_data is None:
        _, resource_type, _, _, decoded = was_inspector.load_rsc5(path)
        if resource_type != 6:
            raise was_inspector.InspectionError(
                f"Expected animation-set resource type 6, found {resource_type}."
            )
    else:
        decoded = decoded_data
    records = records if records is not None else was_inspector.scan_animation_source_records(decoded)
    if record_offset is None:
        record = select_record(records, query)
    else:
        matches = [r for r in records if r['offset'] == record_offset]
        if len(matches) != 1:
            raise was_inspector.InspectionError('Animation record identity was not found.')
        record = matches[0]
    entries: list[dict[str, Any]] = []
    decoded_payloads = 0
    unresolved_payloads = 0
    assembled_tracks = 0
    unassembled_tracks = 0
    static_entries: list[dict[str, Any]] = []
    sequence_entries: list[dict[str, Any]] = []

    targets = was_inspector.scan_padded_pointer_table(decoded, record["track_data_offset"])
    if not targets:
        targets = was_inspector.scan_pointer_array(
            decoded, record["track_data_offset"], record.get("track_array_count", 0)
        )
    for target in targets:
        sequence = was_inspector.parse_compressed_sequence(decoded, target)
        if sequence is None:
            signature = struct.unpack_from("<I", decoded, target)[0]
            static_entries.append(
                {
                    "kind": "static_or_special_object",
                    "offset_hex": f"0x{target:X}",
                    "class_signature": f"0x{signature:08X}",
                }
            )
            continue

        channels: list[dict[str, Any]] = []
        for descriptor in sequence["descriptors"]:
            payloads: list[dict[str, Any]] = []
            for payload_offset in descriptor["component_offsets"]:
                try:
                    payload = was_inspector.decode_payload_samples(decoded, payload_offset)
                except was_inspector.InspectionError as exc:
                    payload = {
                        "status": "invalid",
                        "offset_hex": f"0x{payload_offset:X}",
                        "error": str(exc),
                    }
                if payload["status"] == "decoded":
                    decoded_payloads += 1
                else:
                    unresolved_payloads += 1
                payloads.append(payload)
            if all(payload["status"] == "decoded" for payload in payloads):
                try:
                    assembled = assemble_track_samples(
                        descriptor["kind_name"], payloads, sequence["frame_count"]
                    )
                except was_inspector.InspectionError as exc:
                    assembled = {"status": "invalid", "error": str(exc)}
            else:
                assembled = {"status": "payload_not_decoded"}
            if assembled["status"] == "decoded":
                assembled_tracks += 1
            else:
                unassembled_tracks += 1
            trim_track_samples(assembled, sample_limit)
            for payload in payloads:
                trim_values(payload, sample_limit)
            channels.append(
                {
                    "descriptor_offset_hex": f"0x{descriptor['offset']:X}",
                    "bone_id": descriptor["bone_id"],
                    "bone_id_hex": f"0x{descriptor['bone_id']:04X}",
                    "track_kind": descriptor["kind"],
                    "track_kind_name": descriptor["kind_name"],
                    "encoded_type": descriptor["encoded_type"],
                    "encoded_type_hex": f"0x{descriptor['encoded_type']:02X}",
                    "component_count": descriptor["component_count"],
                    "assembled_track": assembled,
                    "payloads": payloads,
                }
            )
        sequence_entries.append(
            {
                "kind": "compressed_sequence",
                "offset_hex": sequence["offset_hex"],
                "frame_count": sequence["frame_count"],
                "channel_count": sequence["channel_count"],
                "channels": channels,
            }
        )

    # A padded scan can also encounter an adjacent alternate sequence.  The
    # record header identifies the exact clip length; prefer the sequence that
    # matches it before treating the pointers as a multi-block clip.
    exact = [entry for entry in sequence_entries
             if entry.get("frame_count") == record["frame_count"]]
    entries.extend(exact[:1] if exact else sequence_entries)

    # Some arrays include a static companion object next to the real motion
    # sequence. It is not a channel; retain it only for truly static records.
    if not entries:
        entries.extend(static_entries)

    return {
        "schema": "rdr1-animation-studio.was-animation-export.v2",
        "source": str(path.resolve()),
        "animation": {
            "name": record["name"],
            "source_path": record["source_path"],
            "frame_count": record["frame_count"],
            "duration_seconds": record["duration_seconds"],
            "frames_per_second": record["inferred_fps"],
            "sample_period_seconds": round(1.0 / record["inferred_fps"], 9),
            "vector_order": "xyz",
            "quaternion_order": "xyzw",
        },
        "coverage": {
            "decoded_payloads": decoded_payloads,
            "unresolved_payloads": unresolved_payloads,
            "assembled_tracks": assembled_tracks,
            "unassembled_tracks": unassembled_tracks,
            "sample_limit": sample_limit,
        },
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=pathlib.Path, help="Extracted .was file")
    parser.add_argument("animation", help="Exact animation name or a unique name fragment")
    parser.add_argument("-o", "--output", type=pathlib.Path, help="Output JSON path")
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=120,
        help="Maximum values kept per decoded curve; 0 keeps all (default: 120)",
    )
    args = parser.parse_args()
    if args.sample_limit < 0:
        parser.error("--sample-limit must be zero or greater")
    try:
        result = export_animation(args.input, args.animation, args.sample_limit)
        output = args.output or pathlib.Path(result["animation"]["name"] + ".animation.json")
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except (OSError, was_inspector.InspectionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {output}")
    print(
        f"animation={result['animation']['name']} "
        f"frames={result['animation']['frame_count']} "
        f"decoded_payloads={result['coverage']['decoded_payloads']} "
        f"unresolved_payloads={result['coverage']['unresolved_payloads']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
