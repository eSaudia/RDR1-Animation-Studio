#!/usr/bin/env python3
"""Decode verified semantic information from RDR1 resource samples.

This intentionally reports confidence and raw offsets.  RBF0 cutscene files have
stable class/property labels, while the surrounding binary value encoding is not
fully documented; unknown values are therefore never guessed.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import struct
import zlib
from pathlib import Path
from typing import Any

try:
    import zstandard  # optional, needed for camera.rpf entries
except ImportError:  # pragma: no cover - exercised on machines without the extra
    zstandard = None

PRINTABLE = re.compile(rb"[ -~]{4,}")
RSC_MAGICS = {b"RSC\x05", b"RSC\x06", b"RSC\x85", b"RSC\x86"}


def strings(data: bytes) -> list[dict[str, Any]]:
    return [{"offset": match.start(), "value": match.group().decode("ascii", "replace")}
            for match in PRINTABLE.finditer(data)]


def semantic_bucket(value: str) -> str:
    lower = value.lower()
    if value.startswith("rage__cutf"):
        return "type"
    if "camera" in lower or "cut" in lower:
        return "camera"
    if "audio" in lower or "sound" in lower:
        return "audio"
    if "particle" in lower or "effect" in lower:
        return "effect"
    if "subtitle" in lower or "language" in lower:
        return "subtitle"
    if "anim" in lower or "ped" in lower or "character" in lower:
        return "actor"
    if "prop" in lower or "weapon" in lower or "model" in lower:
        return "prop"
    if lower.startswith(("f", "v", "i", "b", "c", "p")):
        return "property"
    return "string"


def decode_rbf0(data: bytes) -> dict[str, Any]:
    header_end = data.find(b"\0", 8)
    class_name = data[8:header_end if header_end >= 0 else min(len(data), 256)].decode("ascii", "replace")
    values = strings(data)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in values:
        grouped.setdefault(semantic_bucket(item["value"]), []).append(item)
    types = [item["value"] for item in values if item["value"].startswith("rage__cutf")]
    events = [item for item in values if item["value"].startswith("rage__cutfObjectIdEvent9")]
    return {
        "format": "RBF0",
        "confidence": "verified container and labels; scalar encodings remain raw",
        "class": class_name,
        "declaredSize": struct.unpack_from("<I", data, 4)[0] if len(data) >= 8 else None,
        "actualSize": len(data),
        "semanticCounts": {
            "objectTypes": len(types),
            "eventRecords": len(events),
            "cameraLabels": len(grouped.get("camera", [])),
            "audioLabels": len(grouped.get("audio", [])),
            "effectLabels": len(grouped.get("effect", [])),
            "subtitleLabels": len(grouped.get("subtitle", [])),
            "actorLabels": len(grouped.get("actor", [])),
            "propLabels": len(grouped.get("prop", [])),
        },
        "labels": grouped,
        "unresolved": [
            "numeric property payloads are preserved as bytes until the RBF value-tag table is validated",
            "event timing and camera transforms require decoding the typed record stream",
        ],
    }


def decode_xml(data: bytes) -> dict[str, Any]:
    import xml.etree.ElementTree as ET
    root = ET.fromstring(data)
    elements: list[dict[str, Any]] = []
    for element in root.iter():
        attrs: dict[str, Any] = {}
        for key, value in element.attrib.items():
            if value.lower() in {"true", "false"}:
                attrs[key] = value.lower() == "true"
            else:
                try:
                    attrs[key] = int(value, 0)
                except ValueError:
                    try:
                        attrs[key] = float(value)
                    except ValueError:
                        attrs[key] = value
        elements.append({"tag": element.tag, "attributes": attrs})
    return {
        "format": "XML",
        "confidence": "verified XML element and attribute values",
        "root": root.tag,
        "elementCount": len(elements),
        "elements": elements,
    }


def decode(data: bytes) -> dict[str, Any]:
    if data[:4] == b"RBF0":
        return decode_rbf0(data)
    if data[:4] in RSC_MAGICS:
        version = struct.unpack_from("<I", data, 4)[0] if len(data) >= 8 else None
        report: dict[str, Any] = {"format": "RSC", "confidence": "verified RSC wrapper", "magic": data[:4].decode("latin1"), "version": version, "actualSize": len(data)}
        if zstandard is not None:
            try:
                payload = data[12 if data[:4] in {b"RSC\x05", b"RSC\x06"} else 16:]
                with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(payload)) as reader:
                    expanded = reader.read()
                report["expandedSize"] = len(expanded)
                report["payload"] = decode(expanded)
                return report
            except Exception as exc:
                report["decompressionError"] = str(exc)
        report["unresolved"] = ["payload compression/resource graph not decoded; install zstandard for RSC05/RSC85 expansion"]
        return report
    if data.lstrip().startswith(b"<?xml") or data.lstrip().startswith(b"<default"):
        return decode_xml(data)
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        if zstandard is None:
            return {"format": "Zstandard", "confidence": "verified frame header", "compressedSize": len(data), "unresolved": ["install zstandard to decode the payload"]}
        expanded = zstandard.ZstdDecompressor().decompress(data)
        result = decode(expanded)
        result["compression"] = {"format": "Zstandard", "compressedSize": len(data), "expandedSize": len(expanded)}
        return result
    found = strings(data)
    identifiers = [item for item in found if re.fullmatch(r"[A-Za-z][A-Za-z0-9_:.]{2,}", item["value"])]
    return {
        "format": "raw", "confidence": "verified byte stream and printable identifiers only", "size": len(data),
        "strings": found, "semanticCounts": {"identifiers": len(identifiers), "animationIdentifiers": sum("_" in item["value"] and any(item["value"].lower().startswith(prefix) for prefix in ("air_", "grd_", "gent_", "gped_", "zomb_")) for item in identifiers)},
        "identifiers": identifiers,
        "unresolved": ["mission VM opcode semantics and typed arguments require an independently validated opcode table"]
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resource", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    report = decode(args.resource.read_bytes())
    report["source"] = str(args.resource)
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
