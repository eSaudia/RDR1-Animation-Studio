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

# RDR's compact VM opcode byte map.  This is the target-indexed table used by
# the open-source RASM RDR backend; names are intentionally kept separate from
# argument decoding so an unknown payload is never presented as fully decoded.
RDR_OPCODE_NAMES = [
    "Nop", "Add", "Sub", "Mult", "Div", "Mod", "Not", "Neg", "CmpEq", "CmpNe", "CmpGt", "CmpGe", "CmpLt", "CmpLe",
    "fAdd", "fSub", "fMult", "fDiv", "fMod", "fNeg", "fCmpEq", "fCmpNe", "fCmpGt", "fCmpGe", "fCmpLt", "fCmpLe",
    "vAdd", "vSub", "vMult", "vDiv", "vNeg", "And", "Or", "Xor", "ItoF", "FtoI", "FtoV", "PushB", "PushB2", "PushB3",
    "Push", "PushF", "Dup", "Drop", "CallNative", "Function", "Return", "pGet", "pSet", "pPeekSet", "ToStack", "FromStack",
    "GetArrayP1", "GetArray1", "SetArray1", "GetLocalP1", "GetLocal1", "SetLocal1", "GetStaticP1", "GetStatic1", "SetStatic1",
    "AddImm1", "GetImm1", "SetImm1", "MultImm1", "PushS", "AddImm2", "GetImm2", "SetImm2", "MultImm2", "GetArrayP2", "GetArray2",
    "SetArray2", "GetLocalP2", "GetLocal2", "SetLocal2", "GetStaticP2", "GetStatic2", "SetStatic2", "GetGlobalP2", "GetGlobal2",
    "SetGlobal2", "Call2", "Call2h1", "Call2h2", "Call2h3", "Call2h4", "Call2h5", "Call2h6", "Call2h7", "Call2h8", "Call2h9",
    "Call2hA", "Call2hB", "Call2hC", "Call2hD", "Call2hE", "Call2hF", "Jump", "JumpFalse", "JumpNe", "JumpEq", "JumpLe",
    "JumpLt", "JumpGe", "JumpGt", "GetGlobalP3", "GetGlobal3", "SetGlobal3", "PushI24", "Switch", "PushString", "PushArray",
    "PushStringNull", "StrCopy", "ItoS", "StrAdd", "StrAddi", "MemCopy", "Catch", "Throw", "pCall", "ReturnP0R0", "ReturnP0R1",
    "ReturnP0R2", "ReturnP0R3", "ReturnP1R0", "ReturnP1R1", "ReturnP1R2", "ReturnP1R3", "ReturnP2R0", "ReturnP2R1", "ReturnP2R2",
    "ReturnP2R3", "ReturnP3R0", "ReturnP3R1", "ReturnP3R2", "ReturnP3R3", "Push_Neg1", "Push_0", "Push_1", "Push_2", "Push_3",
    "Push_4", "Push_5", "Push_6", "Push_7", "PushF_Neg1", "PushF_0", "PushF_1", "PushF_2", "PushF_3", "PushF_4", "PushF_5",
    "PushF_6", "PushF_7"
]

RDR_ONE_BYTE = {
    "Nop", "Add", "Sub", "Mult", "Div", "Mod", "Not", "Neg", "CmpEq", "CmpNe", "CmpGt", "CmpGe", "CmpLt", "CmpLe",
    "fAdd", "fSub", "fMult", "fDiv", "fMod", "fNeg", "fCmpEq", "fCmpNe", "fCmpGt", "fCmpGe", "fCmpLt", "fCmpLe",
    "vAdd", "vSub", "vMult", "vDiv", "vNeg", "And", "Or", "Xor", "ItoF", "FtoI", "FtoV", "Dup", "Drop", "pGet", "pSet",
    "pPeekSet", "ToStack", "FromStack", "PushStringNull", "MemCopy", "Catch", "Throw", "pCall"
}

def _rdr_instruction(data: bytes, offset: int, native_values: list[int] | None = None) -> tuple[dict[str, Any], int] | None:
    """Decode one RDR instruction using the validated RASM size rules."""
    if offset >= len(data):
        return None
    opcode = data[offset]
    name = RDR_OPCODE_NAMES[opcode] if opcode < len(RDR_OPCODE_NAMES) else None
    if name is None:
        return ({"offset": offset, "opcode": opcode, "name": None, "bytes": data[offset:offset + 1].hex(), "confidence": "unknown opcode byte"}, 1)
    size = 1
    args: dict[str, Any] = {}
    if name in RDR_ONE_BYTE:
        pass
    elif name in {"PushB", "AddImm1", "GetImm1", "SetImm1", "MultImm1", "GetArrayP1", "GetArray1", "SetArray1", "GetLocalP1", "GetLocal1", "SetLocal1", "GetStaticP1", "GetStatic1", "SetStatic1", "StrCopy", "ItoS", "StrAdd", "StrAddi"}:
        size = 2; args["value_u8"] = data[offset + 1] if offset + 1 < len(data) else None
    elif name in {"PushB2", "PushS", "AddImm2", "GetImm2", "SetImm2", "MultImm2", "GetArrayP2", "GetArray2", "SetArray2", "GetLocalP2", "GetLocal2", "SetLocal2", "GetStaticP2", "GetStatic2", "SetStatic2", "GetGlobalP2", "GetGlobal2", "SetGlobal2", "CallNative", "Call2", "Call2h1", "Call2h2", "Call2h3", "Call2h4", "Call2h5", "Call2h6", "Call2h7", "Call2h8", "Call2h9", "Call2hA", "Call2hB", "Call2hC", "Call2hD", "Call2hE", "Call2hF", "Return"}:
        size = 3
        if offset + 2 < len(data):
            args["value_u16"] = int.from_bytes(data[offset + 1:offset + 3], "big")
            if name == "CallNative":
                native_data = args["value_u16"]
                args["nativeIndex"] = ((native_data & 0xFF) << 2) & 0x300 | ((native_data >> 8) & 0xFF)
                args["parameterCount"] = (native_data & 0x3E) >> 1
                args["returnsValue"] = bool(native_data & 1)
                if native_values is not None and args["nativeIndex"] < len(native_values):
                    args["nativeHash"] = f"0x{native_values[args['nativeIndex']]:08X}"
            elif name == "Call2" or name.startswith("Call2h"):
                call_type = 0 if name == "Call2" else int(name[-1], 16)
                args["callType"] = call_type
                args["callOffset"] = args["value_u16"] | (call_type << 16)
    elif name in {"PushB3", "GetGlobalP3", "GetGlobal3", "SetGlobal3", "PushI24"}:
        size = 4; args["value_u24"] = int.from_bytes(data[offset + 1:offset + 4], "big") if offset + 3 < len(data) else None
    elif name in {"Push", "PushF"}:
        size = 5
        if offset + 4 < len(data):
            args["value_i32"] = int.from_bytes(data[offset + 1:offset + 5], "big", signed=True)
            if name == "PushF":
                args["value_f32"] = struct.unpack(">f", data[offset + 1:offset + 5])[0]
    elif name == "Function":
        size = 4; args["parameterCount"] = data[offset + 1] if offset + 1 < len(data) else None; args["variableCount"] = int.from_bytes(data[offset + 2:offset + 4], "big") if offset + 3 < len(data) else None
    elif name in {"Jump", "JumpFalse", "JumpNe", "JumpEq", "JumpLe", "JumpLt", "JumpGe", "JumpGt"}:
        size = 3; args["relativeTarget"] = int.from_bytes(data[offset + 1:offset + 3], "big", signed=True) if offset + 2 < len(data) else None; args["target"] = offset + size + (args["relativeTarget"] or 0)
    elif name == "Switch":
        count = data[offset + 1] if offset + 1 < len(data) else 0; size = 2 + count * 6; args["caseCount"] = count
    elif name == "PushString":
        length = data[offset + 1] if offset + 1 < len(data) else 0; size = 2 + length; raw = data[offset + 2:offset + size]; args["length"] = length; args["text"] = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
    elif name == "PushArray":
        length = int.from_bytes(data[offset + 1:offset + 5], "little") if offset + 4 < len(data) else 0; size = 5 + length; args["length"] = length
    elif name.startswith("ReturnP") or name.startswith("Push_") or name.startswith("PushF_"):
        size = 1
    else:
        # Unknown/target-specific compact forms are retained as one byte so
        # the report remains bounded and explicitly marks the uncertainty.
        size = 1
    size = max(1, min(size, len(data) - offset))
    return ({"offset": offset, "opcode": opcode, "name": name, "bytes": data[offset:offset + size].hex(), "args": args, "confidence": "opcode name and size validated against RDR backend"}, size)


def decode_vm(data: bytes, starts: list[int] | None = None) -> dict[str, Any]:
    pointers = []
    for offset in range(0, min(len(data), 0x2000) - 3, 4):
        value = int.from_bytes(data[offset:offset + 4], "little")
        target = value & 0x0FFFFFFF if (value & 0xF0000000) == 0x50000000 else -1
        if 0 <= target < len(data):
            pointers.append({"fieldOffset": offset, "raw": f"0x{value:08X}", "target": target})
    # RDR XSC/CSC resources have a validated script header marker (the RASM
    # backend checks 0xA8D74300 after resource decompression). Generic RSC
    # objects also contain 0x50000000 relative pointers and arbitrary bytes;
    # never label those bytes as VM instructions without the marker.
    header_candidates = [i for i in range(0, min(len(data), 0x4000) - 3, 4)
                         if data[i:i + 4] in {b"\x00C\xD7\xA8", b"\xA8\xD7C\x00"}]
    headers: list[dict[str, Any]] = []
    starts_from_headers: list[int] = []
    native_values: list[int] = []
    for header in header_candidates:
        if header + 44 > len(data):
            continue
        read = lambda pos: int.from_bytes(data[header + pos:header + pos + 4], "big")
        page_base, page_map, code_blocks, code_length = read(0), read(4), read(8), read(12)
        parameter_count, statics_count, statics_ptr, signature = read(16), read(20), read(24), read(28)
        natives_count, natives_ptr = read(32), read(36)
        code_table = code_blocks & 0x0FFFFFFF
        valid = code_length > 0 and code_length <= len(data) and signature == 0x8A019D34 and code_table < len(data)
        block_starts: list[int] = []
        if valid:
            block_count = (code_length + 0x3FFF) >> 14
            for index in range(block_count):
                pos = code_table + index * 4
                if pos + 4 > len(data):
                    valid = False
                    break
                target = int.from_bytes(data[pos:pos + 4], "big") & 0x0FFFFFFF
                if target >= len(data):
                    valid = False
                    break
                block_starts.append(target)
            if natives_ptr & 0x0FFFFFFF < len(data):
                native_start = natives_ptr & 0x0FFFFFFF
                native_end = min(len(data), native_start + natives_count * 4)
                native_values = [int.from_bytes(data[pos:pos + 4], "big") for pos in range(native_start, native_end, 4) if pos + 4 <= len(data)]
        record = {"offset": header, "pageBase": f"0x{page_base:08X}", "codeLength": code_length, "parameterCount": parameter_count, "staticsCount": statics_count, "nativesCount": natives_count, "signature": f"0x{signature:08X}", "codeBlocksOffset": code_table, "staticsOffset": statics_ptr & 0x0FFFFFFF, "nativesOffset": natives_ptr & 0x0FFFFFFF, "codeBlockStarts": block_starts, "valid": valid}
        headers.append(record)
        if valid:
            starts_from_headers.extend(block_starts)
    if not headers or not any(item["valid"] for item in headers):
        return {
            "format": "RDR VM evidence",
            "confidence": "no validated RDR script header found; pointer/opcode bytes are not disassembled",
            "imageBase": "0x50000000",
            "pointerCount": len(pointers),
            "pointers": pointers[:1024],
            "headerCandidates": header_candidates,
            "headers": headers,
            "functions": [],
            "unresolved": ["resource is not a validated XSC/CSC payload (or the script header is nested/obfuscated)", "native hash/name table", "mission-specific event semantics"]
        }
    candidates = sorted(set(starts or starts_from_headers))
    functions = []
    for start in candidates[:256]:
        if start >= len(data):
            continue
        instructions = []
        pos = start
        for _ in range(256):
            decoded = _rdr_instruction(data, pos, native_values)
            if decoded is None:
                break
            ins, size = decoded; instructions.append(ins); pos += size
            if ins.get("name") is None or ins.get("name", "").startswith("Return") or pos - start >= 0x800:
                break
        known = sum(1 for ins in instructions if ins.get("name"))
        if len(instructions) >= 2 and known / len(instructions) >= 0.65:
            functions.append({"start": start, "instructionCount": len(instructions), "knownRatio": round(known / len(instructions), 3), "instructions": instructions})
    return {"format": "RDR VM evidence", "confidence": "opcode names/sizes validated; native table values are raw hashes; control-flow meaning remains partial", "imageBase": "0x50000000", "pointerCount": len(pointers), "pointers": pointers[:1024], "headerCandidates": header_candidates, "headers": headers, "nativeTable": [{"index": i, "hash": f"0x{value:08X}"} for i, value in enumerate(native_values[:4096])], "functions": functions, "unresolved": ["native hash/name table", "script header/code-block boundaries", "encrypted console variants", "mission-specific event semantics"]}


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
        try:
            expanded = zstandard.ZstdDecompressor().decompress(data)
        except Exception as exc:
            return {"format": "Zstandard", "confidence": "verified frame header; payload could not be expanded", "compressedSize": len(data), "decompressionError": str(exc), "unresolved": ["truncated/invalid Zstandard frame"]}
        result = decode(expanded)
        result["compression"] = {"format": "Zstandard", "compressedSize": len(data), "expandedSize": len(expanded)}
        return result
    found = strings(data)
    identifiers = [item for item in found if re.fullmatch(r"[A-Za-z][A-Za-z0-9_:.]{2,}", item["value"])]
    pointers = []
    for offset in range(0, min(len(data), 0x800) - 3, 4):
        value = int.from_bytes(data[offset:offset + 4], "little")
        target = value - 0x50000000
        if 0 <= target < len(data):
            pointers.append({"offset": offset, "raw": f"0x{value:08X}", "target": target})
    histogram: dict[str, int] = {}
    for offset in range(0, len(data) - 3, 4):
        opcode = data[offset]
        histogram[f"0x{opcode:02X}"] = histogram.get(f"0x{opcode:02X}", 0) + 1
    vm = decode_vm(data)
    return {
        "format": "raw", "confidence": "verified byte stream and printable identifiers only", "size": len(data),
        "strings": found, "semanticCounts": {"identifiers": len(identifiers), "animationIdentifiers": sum("_" in item["value"] and any(item["value"].lower().startswith(prefix) for prefix in ("air_", "grd_", "gent_", "gped_", "zomb_")) for item in identifiers)},
        "identifiers": identifiers,
        "vmEvidence": {"imageBase": "0x50000000 (candidate)", "headerPointers": pointers, "wordOpcodeHistogram": dict(sorted(histogram.items(), key=lambda pair: (-pair[1], pair[0]))[:64]), "disassembly": vm},
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
