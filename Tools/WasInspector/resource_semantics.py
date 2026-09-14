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
import os
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

def _normalize_native_hash(value: Any) -> int | None:
    """Normalize native ids from common database JSON representations."""
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value & 0xFFFFFFFF
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text, 0) & 0xFFFFFFFF
        except ValueError:
            return int(text, 16) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return None


def load_native_database_metadata(path: str | Path | None = None) -> tuple[dict[int, dict[str, Any]], str | None]:
    """Load optional RDR1 native names, parameter types, and return types."""
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    env_path = os.environ.get("RDR1_NATIVE_DB")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path(__file__).resolve().parents[2] / "Assets" / "rdr1-natives.json")
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        candidates.append(Path(local_app) / "RDR1AnimationStudio" / "natives.json")
    selected = next((candidate for candidate in candidates if candidate.is_file()), None)
    if selected is None:
        return {}, None
    try:
        raw = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}, str(selected)
    records: Any = raw
    if isinstance(raw, dict):
        for key in ("natives", "commands", "entries", "functions", "data"):
            if key in raw and isinstance(raw[key], (dict, list)):
                records = raw[key]
                break
    result: dict[int, dict[str, Any]] = {}
    def visit(value: Any, key_hint: Any = None) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        native_hash = _normalize_native_hash(value.get("hash"))
        if native_hash is None:
            native_hash = _normalize_native_hash(value.get("nativeHash"))
        if native_hash is None:
            native_hash = _normalize_native_hash(value.get("id"))
        if native_hash is None:
            native_hash = _normalize_native_hash(value.get("value"))
        if native_hash is None:
            native_hash = _normalize_native_hash(key_hint)
        name = value.get("name") or value.get("command") or value.get("function") or value.get("label")
        if native_hash is not None and isinstance(name, str) and name.strip():
            metadata: dict[str, Any] = {"name": name.strip()}
            params = value.get("params")
            if isinstance(params, list):
                metadata["parameterTypes"] = [item.get("type") for item in params if isinstance(item, dict) and isinstance(item.get("type"), str)]
            return_type = value.get("return_type") or value.get("returnType")
            if isinstance(return_type, str) and return_type.strip():
                metadata["returnType"] = return_type.strip()
            result[native_hash] = metadata
        for key, child in value.items():
            if key not in {"hash", "nativeHash", "id", "value", "name", "command", "function", "label", "comment", "params", "return_type"}:
                child_hash = _normalize_native_hash(key)
                if child_hash is not None and isinstance(child, str) and child.strip():
                    result[child_hash] = {"name": child.strip()}
                else:
                    visit(child, key)
    visit(records)
    return result, str(selected)


def load_native_database(path: str | Path | None = None) -> tuple[dict[int, str], str | None]:
    """Compatibility wrapper returning only native hash-to-name mappings."""
    metadata, selected = load_native_database_metadata(path)
    return {native_hash: item["name"] for native_hash, item in metadata.items() if item.get("name")}, selected


def _rdr_instruction(data: bytes, offset: int, native_values: list[int] | None = None,
                     native_names: dict[int, str] | None = None,
                     native_metadata: dict[int, dict[str, Any]] | None = None) -> tuple[dict[str, Any], int] | None:
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
                    native_hash = native_values[args["nativeIndex"]]
                    args["nativeHash"] = f"0x{native_hash:08X}"
                    if native_names and native_hash in native_names:
                        args["nativeName"] = native_names[native_hash]
                    if native_metadata and native_hash in native_metadata:
                        metadata = native_metadata[native_hash]
                        if metadata.get("parameterTypes"):
                            args["nativeParameterTypes"] = metadata["parameterTypes"]
                        if metadata.get("returnType"):
                            args["nativeReturnType"] = metadata["returnType"]
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


def decode_vm(data: bytes, starts: list[int] | None = None,
              native_names: dict[int, str] | None = None,
              native_metadata: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
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
            decoded = _rdr_instruction(data, pos, native_values, native_names, native_metadata)
            if decoded is None:
                break
            ins, size = decoded; instructions.append(ins); pos += size
            if ins.get("name") is None or ins.get("name", "").startswith("Return") or pos - start >= 0x800:
                break
        known = sum(1 for ins in instructions if ins.get("name"))
        if len(instructions) >= 2 and known / len(instructions) >= 0.65:
            functions.append({"start": start, "instructionCount": len(instructions), "knownRatio": round(known / len(instructions), 3), "instructions": instructions})
    native_table = []
    for i, value in enumerate(native_values[:4096]):
        entry: dict[str, Any] = {"index": i, "hash": f"0x{value:08X}"}
        if native_names and value in native_names:
            entry["name"] = native_names[value]
        if native_metadata and value in native_metadata:
            metadata = native_metadata[value]
            if metadata.get("parameterTypes"):
                entry["parameterTypes"] = metadata["parameterTypes"]
            if metadata.get("returnType"):
                entry["returnType"] = metadata["returnType"]
        native_table.append(entry)
    unresolved = ["script header/code-block boundaries", "encrypted console variants", "mission-specific event semantics"]
    if not native_names:
        unresolved.insert(0, "native hash/name table")
    return {"format": "RDR VM evidence", "confidence": "opcode names/sizes validated; native hashes resolved when an optional database is supplied; control-flow meaning remains partial", "imageBase": "0x50000000", "pointerCount": len(pointers), "pointers": pointers[:1024], "headerCandidates": header_candidates, "headers": headers, "nativeTable": native_table, "functions": functions, "unresolved": unresolved}


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


def decode(data: bytes, native_names: dict[int, str] | None = None,
           native_metadata: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
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
                report["payload"] = decode(expanded, native_names, native_metadata)
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
        result = decode(expanded, native_names, native_metadata)
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
    vm = decode_vm(data, native_names=native_names, native_metadata=native_metadata)
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
    parser.add_argument("--native-db", type=Path, help="Optional RDR1 native hash database JSON")
    args = parser.parse_args()
    native_metadata, native_path = load_native_database_metadata(args.native_db)
    native_names = {native_hash: item["name"] for native_hash, item in native_metadata.items() if item.get("name")}
    report = decode(args.resource.read_bytes(), native_names, native_metadata)
    report["nativeDatabase"] = {"path": native_path, "count": len(native_metadata)}
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
