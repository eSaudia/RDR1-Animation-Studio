#!/usr/bin/env python3
"""Decode verified semantic information from RDR1 resource samples.

This intentionally reports confidence and raw offsets.  RBF0 cutscene files have
stable class/property labels, while the surrounding binary value encoding is not
fully documented; unknown values are therefore never guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

from rsc85_crypto import decrypt_payload

# Keep the project's bundled, offline dependencies available when this helper
# is launched directly or by the desktop app.  The decoder must not require a
# machine-wide Python package install just to expand an RSC85 payload.
_LOCAL_DEPS = Path(__file__).resolve().parent / "_deps"
if _LOCAL_DEPS.is_dir():
    _dependency_path = str(_LOCAL_DEPS)
    if _dependency_path not in sys.path:
        sys.path.insert(0, _dependency_path)

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
    "PushF_6", "PushF_7", "PatchRet", "PatchTrap0", "PatchTrap1", "PatchTrap2", "PatchTrap3", "PatchTrap4", "PatchTrap5",
    "PatchTrap6", "PatchTrap7", "PatchTrap8", "PatchTrap9", "PatchTrapA", "PatchTrapB", "PatchTrapC", "PatchTrapD", "PatchTrapE",
    "PatchTrapF", "CallPatch", "CallOutOfPatch", "LoadRef", "StoreRef", "StoreVector", "MakeVector"
]

RDR_ONE_BYTE = {
    "Nop", "Add", "Sub", "Mult", "Div", "Mod", "Not", "Neg", "CmpEq", "CmpNe", "CmpGt", "CmpGe", "CmpLt", "CmpLe",
    "fAdd", "fSub", "fMult", "fDiv", "fMod", "fNeg", "fCmpEq", "fCmpNe", "fCmpGt", "fCmpGe", "fCmpLt", "fCmpLe",
    "vAdd", "vSub", "vMult", "vDiv", "vNeg", "And", "Or", "Xor", "ItoF", "FtoI", "FtoV", "Dup", "Drop", "pGet", "pSet",
    "pPeekSet", "ToStack", "FromStack", "PushStringNull", "MemCopy", "Catch", "Throw", "pCall",
    # RASM's post-155 extension opcodes are compact, operand-free instructions.
    # Keep them explicit so a decoded report can distinguish these known VM
    # operations from an unknown byte and continue scanning the bounded stream.
    "PatchRet", "PatchTrap0", "PatchTrap1", "PatchTrap2", "PatchTrap3", "PatchTrap4", "PatchTrap5", "PatchTrap6",
    "PatchTrap7", "PatchTrap8", "PatchTrap9", "PatchTrapA", "PatchTrapB", "PatchTrapC", "PatchTrapD", "PatchTrapE",
    "PatchTrapF", "CallPatch", "CallOutOfPatch", "LoadRef", "StoreRef", "StoreVector", "MakeVector"
}

MISSION_DOMAINS = ("camera", "actor", "ai", "prop", "audio", "animation", "timing")

# These are deliberately conservative lexical hints, not a replacement for a
# native signature table or a decoded mission record schema.  A domain is only
# emitted when the source supplies a resolved name/identifier; raw hashes and
# arbitrary bytes remain unresolved.
NATIVE_DOMAIN_TOKENS = {
    "camera": ("camera", "cam_", "cutscene", "cinematic", "shot"),
    "actor": ("actor", "ped", "player", "character", "npc", "animal"),
    "ai": ("task_", "brain", "behavior", "behaviour", "react", "attack", "patrol", "cover", "target", "capability"),
    "prop": ("prop", "object", "weapon", "vehicle", "model", "attach", "locator"),
    "audio": ("audio", "sound", "voice", "speech", "shout", "music", "subtitle"),
    "animation": ("anim", "motion", "posture", "gesture", "walk", "run", "idle", "clip"),
    "timing": ("time", "delay", "duration", "timer", "timeout", "phase", "sequence", "event"),
}

RESOURCE_DOMAIN_TOKENS = {
    **NATIVE_DOMAIN_TOKENS,
    "camera": ("camera", "cam", "cutscene", "cinematic", "shot", "gringo_camera"),
    "actor": ("actor", "ped", "player", "character", "npc", "animal", "john", "jack"),
    "ai": ("ai", "brain", "behavior", "behaviour", "react", "attack", "patrol", "cover", "target", "capability", "allowtalking"),
    "prop": ("prop", "object", "weapon", "vehicle", "model", "attach", "locator", "bone"),
    "audio": ("audio", "sound", "voice", "speech", "shout", "music", "subtitle"),
    "animation": ("anim", "motion", "posture", "gesture", "walk", "run", "idle", "clip"),
    "timing": ("time", "delay", "duration", "timer", "timeout", "phase", "sequence", "event", "min_", "max_"),
}


def classify_domain_hints(value: str, tokens: dict[str, tuple[str, ...]]) -> list[str]:
    """Return stable domain candidates for a validated name/identifier."""
    lower = value.lower()
    return [domain for domain in MISSION_DOMAINS
            if any(token in lower for token in tokens.get(domain, ()))]


def native_domain_report(native_usage: dict[tuple[int, str | None], dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Summarize resolved native calls with explicitly heuristic domain hints."""
    counts = {domain: {"callCount": 0, "nativeCount": 0} for domain in MISSION_DOMAINS}
    entries: list[dict[str, Any]] = []
    for usage in sorted(native_usage.values(), key=lambda item: (-item["count"], item["nativeIndex"])):
        name = usage.get("name")
        if not isinstance(name, str) or not name:
            continue
        domains = classify_domain_hints(name, NATIVE_DOMAIN_TOKENS)
        if not domains:
            continue
        entry = dict(usage)
        entry["domains"] = domains
        entry["evidence"] = "resolved native name lexical hint; verify against native signature and mission records"
        entries.append(entry)
        for domain in domains:
            counts[domain]["callCount"] += int(usage.get("count", 0) or 0)
            counts[domain]["nativeCount"] += 1
    return entries, counts


def resource_domain_report(identifiers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group printable resource identifiers into reviewable domain hints."""
    values: dict[str, set[str]] = {domain: set() for domain in MISSION_DOMAINS}
    for item in identifiers:
        value = item.get("value") if isinstance(item, dict) else None
        if not isinstance(value, str):
            continue
        for domain in classify_domain_hints(value, RESOURCE_DOMAIN_TOKENS):
            values[domain].add(value)
    return {domain: {"uniqueIdentifierCount": len(names), "identifiers": sorted(names)[:128],
                     "evidence": "printable resource identifier lexical hint; payload fields remain unresolved"}
            for domain, names in values.items() if names}


def function_domain_report(functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach resolved-native domain hints to their containing VM functions."""
    result: list[dict[str, Any]] = []
    for function in functions:
        domain_calls: dict[str, int] = {}
        domain_native_indexes: dict[str, set[int]] = {}
        native_calls = function.get("nativeCalls", [])
        if not isinstance(native_calls, list):
            native_calls = []
        for call in native_calls:
            if not isinstance(call, dict) or not isinstance(call.get("nativeName"), str):
                continue
            domains = classify_domain_hints(call["nativeName"], NATIVE_DOMAIN_TOKENS)
            if not domains:
                continue
            call["domains"] = domains
            call["domainEvidence"] = "resolved native name lexical hint; verify against native signature and mission records"
            native_index = call.get("nativeIndex")
            for domain in domains:
                domain_calls[domain] = domain_calls.get(domain, 0) + 1
                if isinstance(native_index, int):
                    domain_native_indexes.setdefault(domain, set()).add(native_index)
        function["domainUsage"] = {
            domain: {"nativeCallCount": count, "nativeIndexes": sorted(domain_native_indexes.get(domain, set()))}
            for domain, count in sorted(domain_calls.items())
        }
        if domain_calls:
            result.append({"functionIndex": function.get("index"), "label": function.get("label"),
                           "start": function.get("start"), "domains": function["domainUsage"],
                           "evidence": "resolved native names grouped within a validated function boundary"})
    return result


def mission_domain_correlation(resource_hints: dict[str, dict[str, Any]], vm_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Join resource-identifier hints with function-scoped native evidence."""
    function_entries = vm_report.get("functionDomainUsage", [])
    aggregate = vm_report.get("domainUsage", {})
    result: dict[str, dict[str, Any]] = {}
    for domain in MISSION_DOMAINS:
        resource = resource_hints.get(domain, {}) if isinstance(resource_hints, dict) else {}
        native = aggregate.get(domain, {}) if isinstance(aggregate, dict) else {}
        functions = [entry for entry in function_entries if isinstance(entry, dict) and domain in entry.get("domains", {})] if isinstance(function_entries, list) else []
        if not resource and not native.get("callCount") and not functions:
            continue
        result[domain] = {
            "resourceIdentifierCount": resource.get("uniqueIdentifierCount", 0),
            "resourceIdentifiers": resource.get("identifiers", []),
            "nativeCallCount": native.get("callCount", 0),
            "nativeCount": native.get("nativeCount", 0),
            "functions": functions,
            "confidence": "resolved-native and/or printable-identifier evidence; typed mission record correlation remains unresolved",
        }
    return result


def build_typed_record_candidates(identifiers: list[dict[str, Any]], limit: int = 4096) -> list[dict[str, Any]]:
    """Build bounded, reviewable record candidates from validated labels.

    These are *candidates*, not decoded game objects.  A printable identifier
    can tell us that a resource likely participates in a camera/actor/etc.
    domain, but it cannot prove the binary field layout or runtime meaning.
    Keeping the source offset and evidence text makes the next reverse-
    engineering pass reproducible without silently promoting a name to a
    semantic fact.
    """
    candidates: list[dict[str, Any]] = []
    for item in identifiers[:limit]:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if not isinstance(value, str) or not value:
            continue
        domains = classify_domain_hints(value, RESOURCE_DOMAIN_TOKENS)
        if not domains:
            continue
        lower = value.lower()
        if value.startswith("rage__cutfObjectIdEvent9"):
            record_type = "cutsceneEvent"
        elif value.startswith("rage__cutf"):
            record_type = "cutsceneType"
        elif any(token in lower for token in ("time", "delay", "duration", "timeout", "phase")):
            record_type = "timingField"
        else:
            record_type = "resourceIdentifier"
        candidates.append({
            "recordType": record_type,
            "value": value,
            "sourceOffset": item.get("offset"),
            "domains": domains,
            "confidence": "lexical-candidate",
            "evidence": "printable identifier matched conservative domain tokens; binary record fields remain unresolved",
        })
    return candidates


def correlate_trace_to_records(trace: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-reference trace domain evidence with candidate resource records."""
    trace_events = trace.get("events", []) if isinstance(trace, dict) else []
    if not isinstance(trace_events, list):
        trace_events = []
    event_counts = {domain: 0 for domain in MISSION_DOMAINS}
    candidate_counts = {domain: 0 for domain in MISSION_DOMAINS}
    matched_events = {domain: 0 for domain in MISSION_DOMAINS}
    candidate_values: dict[str, list[str]] = {domain: [] for domain in MISSION_DOMAINS}
    for event in trace_events:
        if not isinstance(event, dict):
            continue
        domains = event.get("domains", [])
        if not isinstance(domains, list):
            continue
        for domain in domains:
            if domain in event_counts:
                event_counts[domain] += 1
    for candidate in candidates[:4096]:
        if not isinstance(candidate, dict):
            continue
        domains = candidate.get("domains", [])
        if not isinstance(domains, list):
            continue
        value = candidate.get("value")
        for domain in domains:
            if domain not in candidate_counts:
                continue
            candidate_counts[domain] += 1
            if isinstance(value, str) and len(candidate_values[domain]) < 32 and value not in candidate_values[domain]:
                candidate_values[domain].append(value)
    for domain in MISSION_DOMAINS:
        if event_counts[domain] and candidate_counts[domain]:
            matched_events[domain] = event_counts[domain]
    matched_domains = [domain for domain in MISSION_DOMAINS if matched_events[domain]]
    if matched_domains:
        status = "matched-domain-evidence"
    elif any(event_counts.values()):
        status = "trace-domain-evidence-without-resource-candidate"
    elif any(candidate_counts.values()):
        status = "resource-candidate-without-trace-domain-evidence"
    else:
        status = "no-domain-evidence"
    return {
        "format": "RDR mission trace/resource correlation",
        "status": status,
        "confidence": "domain-level lexical cross-reference only; event timing, payload offsets, and runtime identity remain unresolved",
        "traceEventCount": len(trace_events),
        "candidateCount": len(candidates),
        "matchedDomains": matched_domains,
        "domains": {
            domain: {
                "traceEventCount": event_counts[domain],
                "candidateCount": candidate_counts[domain],
                "matchedEventCount": matched_events[domain],
                "candidateValues": candidate_values[domain],
            }
            for domain in MISSION_DOMAINS
            if event_counts[domain] or candidate_counts[domain]
        },
        "unresolved": ["runtime branch conditions and loop paths", "event timing and payload semantics", "camera transforms, AI arguments, props, and audio codec fields"],
    }


def build_mission_record_index(candidates: list[dict[str, Any]], trace: dict[str, Any]) -> dict[str, Any]:
    """Create a bounded index that makes candidate/trace relationships queryable.

    A record is only an evidence record until its binary layout is validated.
    The index nevertheless gives the UI and later tooling stable ids, source
    offsets, domain counts, and capped trace-event references instead of
    requiring consumers to scan a large semantic report repeatedly.
    """
    trace_events = trace.get("events", []) if isinstance(trace, dict) else []
    if not isinstance(trace_events, list):
        trace_events = []
    events_by_domain: dict[str, list[int]] = {domain: [] for domain in MISSION_DOMAINS}
    for sequence, event in enumerate(trace_events):
        if not isinstance(event, dict):
            continue
        domains = event.get("domains", [])
        if not isinstance(domains, list):
            continue
        for domain in domains:
            if domain in events_by_domain and len(events_by_domain[domain]) < 4096:
                events_by_domain[domain].append(int(event.get("sequence", sequence)))
    type_counts: dict[str, int] = {}
    domain_counts = {domain: 0 for domain in MISSION_DOMAINS}
    records: list[dict[str, Any]] = []
    referenced_event_count = 0
    for index, candidate in enumerate(candidates[:4096]):
        if not isinstance(candidate, dict):
            continue
        record_type = candidate.get("recordType") if isinstance(candidate.get("recordType"), str) else "resourceIdentifier"
        value = candidate.get("value") if isinstance(candidate.get("value"), str) else ""
        source_offset = candidate.get("sourceOffset")
        record_id = f"{record_type}:{source_offset}" if isinstance(source_offset, int) else f"{record_type}:{index}"
        domains = [domain for domain in candidate.get("domains", []) if domain in MISSION_DOMAINS] if isinstance(candidate.get("domains"), list) else []
        trace_sequences = sorted({sequence for domain in domains for sequence in events_by_domain[domain]})[:32]
        if trace_sequences:
            referenced_event_count += 1
        type_counts[record_type] = type_counts.get(record_type, 0) + 1
        for domain in domains:
            domain_counts[domain] += 1
        records.append({
            "recordId": record_id,
            "recordType": record_type,
            "value": value,
            "sourceOffset": source_offset,
            "domains": domains,
            "traceEventSequences": trace_sequences,
            "confidence": candidate.get("confidence", "lexical-candidate"),
            "evidence": candidate.get("evidence", "printable identifier evidence"),
        })
    return {
        "format": "RDR mission record index",
        "confidence": "bounded candidate index with capped source-order trace references; no runtime object identity or typed payload is claimed",
        "recordCount": len(records),
        "typeCounts": dict(sorted(type_counts.items())),
        "domainCounts": {domain: count for domain, count in domain_counts.items() if count},
        "traceEventCount": len(trace_events),
        "recordsWithTraceReferences": referenced_event_count,
        "records": records,
        "unresolved": ["runtime branch conditions and loop paths", "event timing and payload semantics", "camera transforms, AI arguments, props, and audio codec fields"],
    }


def build_mission_trace(functions: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a bounded source-order trace of calls, branches, and returns."""
    events: list[dict[str, Any]] = []
    opcode_counts: dict[str, int] = {}
    native_call_count = 0
    resolved_native_call_count = 0
    unresolved_native_call_count = 0
    call2_count = 0
    branch_count = 0
    return_count = 0
    event_limit = 16384
    events_truncated = False
    branch_names = {"Jump", "JumpFalse", "JumpNe", "JumpEq", "JumpLe", "JumpLt", "JumpGe", "JumpGt"}
    for function in functions:
        function_index = function.get("index")
        label = function.get("label")
        instructions = function.get("instructions", [])
        if not isinstance(instructions, list):
            continue
        for instruction in instructions:
            if not isinstance(instruction, dict):
                continue
            name = instruction.get("name")
            if not isinstance(name, str):
                continue
            opcode_counts[name] = opcode_counts.get(name, 0) + 1
            args = instruction.get("args", {})
            if not isinstance(args, dict):
                args = {}
            event: dict[str, Any] | None = None
            if name == "CallNative":
                native_call_count += 1
                if isinstance(args.get("nativeHash"), str):
                    resolved_native_call_count += 1
                else:
                    unresolved_native_call_count += 1
                event = {"kind": "nativeCall", "functionIndex": function_index, "functionLabel": label,
                         "offset": instruction.get("offset"), "nativeIndex": args.get("nativeIndex"),
                         "nativeHash": args.get("nativeHash"), "parameterCount": args.get("parameterCount"),
                         "returnsValue": args.get("returnsValue"), "nativeResolved": isinstance(args.get("nativeHash"), str)}
                if "nativeName" in args:
                    event["nativeName"] = args["nativeName"]
                if "domains" in instruction:
                    event["domains"] = instruction["domains"]
                elif isinstance(args.get("nativeName"), str):
                    domains = classify_domain_hints(args["nativeName"], NATIVE_DOMAIN_TOKENS)
                    if domains:
                        event["domains"] = domains
            elif name == "Call2" or name.startswith("Call2h"):
                call2_count += 1
                event = {"kind": "call2", "functionIndex": function_index, "functionLabel": label,
                         "offset": instruction.get("offset"), "callType": args.get("callType", 0),
                         "targetOffset": args.get("callOffset"), "targetFunctionIndex": args.get("targetFunctionIndex"),
                         "targetResolved": isinstance(args.get("targetFunctionIndex"), int)}
            elif name in branch_names:
                branch_count += 1
                event = {"kind": "branch", "functionIndex": function_index, "functionLabel": label,
                         "offset": instruction.get("offset"), "opcode": name,
                         "targetOffset": args.get("target")}
            elif name.startswith("Return"):
                return_count += 1
                event = {"kind": "return", "functionIndex": function_index, "functionLabel": label,
                         "offset": instruction.get("offset"), "opcode": name}
            if event is not None:
                event["sequence"] = len(events)
                events.append(event)
                if len(events) >= event_limit:
                    events_truncated = True
                    break
        if len(events) >= event_limit:
            break
    function_summary = [{key: function.get(key) for key in (
        "index", "label", "start", "end", "parameterCount", "variableCount",
        "instructionCount", "nativeCallCount", "outgoingCallCount", "outgoingFunctions",
        "incomingCallCount", "incomingFunctions", "domainUsage")}
        for function in functions]
    return {
        "format": "RDR mission trace",
        "confidence": "bounded source-order trace of validated VM instructions; this is not runtime execution or a fully decoded mission timeline",
        "ordering": "function index, then instruction offset",
        "eventLimit": event_limit,
        "eventsTruncated": events_truncated,
        "eventCount": len(events),
        "nativeCallCount": native_call_count,
        "resolvedNativeCallCount": resolved_native_call_count,
        "unresolvedNativeCallCount": unresolved_native_call_count,
        "call2Count": call2_count,
        "branchCount": branch_count,
        "returnCount": return_count,
        "opcodeCounts": dict(sorted(opcode_counts.items(), key=lambda item: (-item[1], item[0]))),
        "functions": function_summary,
        "events": events,
        "unresolved": ["runtime branch conditions and loop paths", "event timing and payload semantics", "camera transforms, AI arguments, props, and audio codec fields"],
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
    # A malformed or accidentally exported database can be enormous (the
    # user's current natives.json is over a gigabyte).  Loading it with
    # json.loads would allocate several copies and take down the viewer before
    # a mission can be decoded.  Keep the selected path visible in the report,
    # but retain hashes when the database is too large for a bounded read.
    try:
        if selected.stat().st_size > 128 * 1024 * 1024:
            return {}, str(selected)
    except OSError:
        return {}, str(selected)
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
                        expected_parameters = metadata.get("parameterTypes")
                        expected_return = metadata.get("returnType")
                        if expected_parameters:
                            args["signatureExpectedParameters"] = len(expected_parameters)
                        if expected_return:
                            args["signatureExpectedReturnsValue"] = expected_return.lower() not in {"void", "none"}
                        parameter_match = not expected_parameters or len(expected_parameters) == args["parameterCount"]
                        return_match = not expected_return or (expected_return.lower() in {"void", "none"}) == (not args["returnsValue"])
                        args["signatureStatus"] = "match" if parameter_match and return_match else "mismatch"
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
              native_metadata: dict[int, dict[str, Any]] | None = None,
              object_start: int | None = None) -> dict[str, Any]:
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
    legacy_header_candidates = list(header_candidates)
    headers: list[dict[str, Any]] = []
    starts_from_headers: list[int] = []
    native_values: list[int] = []
    # The compact script header gives the code length, while function markers
    # are embedded in that code stream. Keep a separate scan bound so static
    # storage and trailing padding cannot be mistaken for bytecode.
    code_scan_end: int | None = None

    # RSC85 mission resources use the compact resource-script header that
    # Magic-RDR reads at FlagInfo.RSC85_ObjectStart.  The PC/Switch payload is
    # little-endian even though the bytecode itself keeps the established RDR
    # instruction encodings.  Validate all pointer/count fields before using
    # the object as a code source; this prevents arbitrary expanded resources
    # from being labelled as scripts.
    if object_start is not None and 0 <= object_start <= len(data) - 40:
        words = struct.unpack_from("<10I", data, object_start)
        script_magic, sub_header, code_pointer, code_length, parameter_count, static_count, static_pointer, global_version, native_count, native_pointer = words
        to_offset = lambda value: value & 0x0FFFFFFF if (value >> 28) == 5 else value
        sub_offset = to_offset(sub_header)
        code_offset = to_offset(code_pointer)
        static_offset = to_offset(static_pointer)
        native_offset = to_offset(native_pointer)
        page_count = (code_length + 0x3FFF) >> 14 if code_length else 0
        block_starts: list[int] = []
        valid = (
            script_magic in {0x00ACE444, 0x00ACE445}
            and code_length > 0
            and code_length <= len(data)
            and 0 <= code_offset <= len(data) - page_count * 4
            and parameter_count <= 0x100
            and static_count <= 0x100000
            and 0 <= static_offset <= len(data) - static_count * 4
            and native_count <= 0x10000
            and 0 <= native_offset <= len(data) - native_count * 4
        )
        if valid:
            for index in range(page_count):
                target = to_offset(struct.unpack_from("<I", data, code_offset + index * 4)[0])
                if target >= len(data):
                    valid = False
                    break
                block_starts.append(target)
        if valid:
            native_values = [struct.unpack_from("<I", data, native_offset + index * 4)[0]
                             for index in range(native_count)]
            code_scan_end = min(len(data), code_length)
        static_values: list[int] = []
        static_nonzero_count = 0
        static_sha256: str | None = None
        if valid:
            static_bytes = data[static_offset:static_offset + static_count * 4]
            static_sha256 = hashlib.sha256(static_bytes).hexdigest().upper()
            # Keep the report bounded even for scripts with very large static
            # tables; the full bytes remain available in payload.expanded.bin.
            static_values = [struct.unpack_from("<I", static_bytes, index * 4)[0]
                             for index in range(min(static_count, 4096))]
            static_nonzero_count = sum(1 for index in range(static_count)
                                       if struct.unpack_from("<I", static_bytes, index * 4)[0] != 0)
        header_candidates.append(object_start)
        headers.append({
            "offset": object_start,
            "format": "RDR resource script",
            "endian": "little",
            "magic": f"0x{script_magic:08X}",
            "subHeaderOffset": sub_offset,
            "codePointerOffset": code_offset,
            "codeLength": code_length,
            "parameterCount": parameter_count,
            "staticsCount": static_count,
            "staticsOffset": static_offset,
            "staticInitialValues": static_values,
            "staticValuesTruncated": static_count > len(static_values),
            "staticNonZeroCount": static_nonzero_count,
            "staticTableSha256": static_sha256,
            "globalVersion": f"0x{global_version:08X}",
            "nativesCount": native_count,
            "nativesOffset": native_offset,
            "pageCount": page_count,
            "codeBlockStarts": block_starts,
            "valid": valid,
            "validation": "resource object-start header, page table, and native table fit expanded RSC85 payload" if valid else "resource-script fields failed bounds or magic validation",
        })
        if valid:
            starts_from_headers.extend(block_starts)
    for header in legacy_header_candidates:
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
            "nativeUsage": [],
            "nativeDomainUsage": [],
            "domainUsage": {domain: {"callCount": 0, "nativeCount": 0} for domain in MISSION_DOMAINS},
            "functionDomainUsage": [],
            "missionTrace": {"format": "RDR mission trace", "confidence": "no validated VM functions", "eventCount": 0, "events": [], "functions": []},
            "callGraph": [],
            "callGraphEdgeCount": 0,
            "callGraphResolvedCallCount": 0,
            "callGraphUnresolvedCallCount": 0,
            "callReferences": [],
            "unresolved": ["resource is not a validated XSC/CSC payload (or the script header is nested/obfuscated)", "native hash/name table", "mission-specific event semantics"]
        }
    # RDR's code table identifies page entry points, while each page contains
    # a sequence of `Function` markers. Discover those markers with the same
    # validated instruction-size decoder before disassembling each function.
    # This remains evidence-only: a marker is a boundary, not a decompiled
    # function name or mission event semantic.
    discovered_function_starts: list[int] = []
    if code_scan_end is not None and starts_from_headers:
        scan_pos = max(0, min(starts_from_headers))
        while scan_pos < code_scan_end:
            decoded = _rdr_instruction(data, scan_pos, native_values, native_names, native_metadata)
            if decoded is None:
                break
            instruction, size = decoded
            if instruction.get("name") == "Function":
                discovered_function_starts.append(scan_pos)
            scan_pos += max(1, size)
    candidates = sorted(set(starts if starts else starts_from_headers))
    if discovered_function_starts:
        candidates = sorted(set(candidates).union(discovered_function_starts))
    function_end = code_scan_end if code_scan_end is not None else len(data)
    function_index_by_start = {start: index for index, start in enumerate(candidates[:4096])}
    functions = []
    native_usage: dict[tuple[int, str | None], dict[str, Any]] = {}
    call_references: list[dict[str, Any]] = []
    signature_mismatches = 0
    for function_index, start in enumerate(candidates[:4096]):
        if start >= len(data):
            continue
        next_starts = [candidate for candidate in candidates if candidate > start]
        boundary = min(next_starts[0] if next_starts else function_end, function_end)
        instructions = []
        pos = start
        terminated_by = "boundary"
        for _ in range(8192):
            if pos >= boundary:
                break
            decoded = _rdr_instruction(data, pos, native_values, native_names, native_metadata)
            if decoded is None:
                break
            ins, size = decoded; instructions.append(ins); pos += size
            if ins.get("name") is None:
                terminated_by = "unknown-opcode"
                break
            if ins.get("name", "").startswith("Return"):
                terminated_by = "return"
                break
        else:
            terminated_by = "instruction-limit"
        known = sum(1 for ins in instructions if ins.get("name"))
        if len(instructions) >= 2 and known / len(instructions) >= 0.65:
            native_calls: list[dict[str, Any]] = []
            control_flow: list[dict[str, Any]] = []
            for ins in instructions:
                ins_name = ins.get("name")
                args = ins.get("args", {})
                if ins_name == "CallNative" and "nativeHash" in args:
                    usage_key = (args["nativeIndex"], args["nativeHash"])
                    usage = native_usage.setdefault(usage_key, {"nativeIndex": args["nativeIndex"], "hash": args["nativeHash"], "count": 0})
                    usage["count"] += 1
                    if args.get("signatureStatus") == "mismatch":
                        usage["signatureMismatches"] = usage.get("signatureMismatches", 0) + 1
                        signature_mismatches += 1
                    if "nativeName" in args:
                        usage["name"] = args["nativeName"]
                    native_calls.append({key: args[key] for key in ("nativeIndex", "nativeHash", "nativeName", "nativeParameterTypes", "nativeReturnType", "signatureStatus", "signatureExpectedParameters", "signatureExpectedReturnsValue") if key in args})
                if ins_name in {"Jump", "JumpFalse", "JumpNe", "JumpEq", "JumpLe", "JumpLt", "JumpGe", "JumpGt"} and isinstance(args.get("target"), int):
                    edge = {"from": ins.get("offset"), "to": args["target"], "kind": ins_name}
                    control_flow.append(edge)
                    call_references.append({"function": start, **edge})
                elif ins_name == "Call2" or (isinstance(ins_name, str) and ins_name.startswith("Call2h")):
                    if isinstance(args.get("callOffset"), int):
                        target_function = function_index_by_start.get(args["callOffset"])
                        if target_function is not None:
                            args["targetFunctionIndex"] = target_function
                            args["targetFunctionStart"] = args["callOffset"]
                        reference = {"function": start, "from": ins.get("offset"), "to": args["callOffset"], "kind": "call2", "callType": args.get("callType", 0)}
                        if target_function is not None:
                            reference["targetFunctionIndex"] = target_function
                        call_references.append(reference)
            function_header = next((item for item in instructions if item.get("name") == "Function"), None)
            function_args = function_header.get("args", {}) if function_header else {}
            functions.append({"index": function_index, "label": "main" if start == 0 else f"Function_{function_index}",
                              "start": start, "end": pos,
                              "parameterCount": function_args.get("parameterCount"),
                              "variableCount": function_args.get("variableCount"),
                              "instructionCount": len(instructions), "knownRatio": round(known / len(instructions), 3),
                              "nativeCallCount": len(native_calls), "nativeCalls": native_calls,
                              "controlFlowEdges": control_flow, "outgoingCallCount": 0, "outgoingFunctions": [],
                              "incomingCallCount": 0, "incomingFunctions": [], "terminatedBy": terminated_by,
                              "instructions": instructions})

    # Resolve the bounded Call2 references into an aggregate function graph.
    # Function markers provide stable structural identities; the generated
    # labels are intentionally not treated as mission-semantic names.
    function_index_by_actual_start = {item["start"]: item["index"] for item in functions}
    graph_counts: dict[tuple[int, int, int], int] = {}
    unresolved_call_count = 0
    for reference in call_references:
        if reference.get("kind") != "call2":
            continue
        caller_index = function_index_by_actual_start.get(reference.get("function"))
        callee_index = reference.get("targetFunctionIndex")
        if caller_index is None or callee_index not in function_index_by_actual_start.values():
            unresolved_call_count += 1
            continue
        call_type = int(reference.get("callType", 0))
        key = (caller_index, int(callee_index), call_type)
        graph_counts[key] = graph_counts.get(key, 0) + 1
    function_records_by_index = {item["index"]: item for item in functions}
    call_graph: list[dict[str, Any]] = []
    for (caller_index, callee_index, call_type), count in sorted(graph_counts.items()):
        caller = function_records_by_index[caller_index]
        callee = function_records_by_index[callee_index]
        call_graph.append({"callerFunctionIndex": caller_index, "callerLabel": caller["label"],
                           "callerStart": caller["start"], "calleeFunctionIndex": callee_index,
                           "calleeLabel": callee["label"], "calleeStart": callee["start"],
                           "callType": call_type, "count": count})
    outgoing: dict[int, dict[int, int]] = {}
    incoming: dict[int, dict[int, int]] = {}
    for edge in call_graph:
        caller_index = edge["callerFunctionIndex"]
        callee_index = edge["calleeFunctionIndex"]
        caller_outgoing = outgoing.setdefault(caller_index, {})
        caller_outgoing[callee_index] = caller_outgoing.get(callee_index, 0) + edge["count"]
        callee_incoming = incoming.setdefault(callee_index, {})
        callee_incoming[caller_index] = callee_incoming.get(caller_index, 0) + edge["count"]
    for function in functions:
        function_index = function["index"]
        outgoing_map = outgoing.get(function_index, {})
        incoming_map = incoming.get(function_index, {})
        function["outgoingCallCount"] = sum(outgoing_map.values())
        function["outgoingFunctions"] = sorted(outgoing_map)
        function["incomingCallCount"] = sum(incoming_map.values())
        function["incomingFunctions"] = sorted(incoming_map)
    native_domain_usage, domain_usage = native_domain_report(native_usage)
    function_domain_usage = function_domain_report(functions)
    mission_trace = build_mission_trace(functions)
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
    unresolved = ["encrypted console variants", "mission-specific event semantics"]
    if not any(item.get("format") == "RDR resource script" and item.get("valid") for item in headers):
        unresolved.insert(0, "script header/code-block boundaries")
    if not native_names:
        unresolved.insert(0, "native hash/name table")
    return {"format": "RDR VM evidence", "confidence": "opcode names/sizes, embedded function boundaries, bounded internal Call2 graph, and source-order mission trace validated; resolved native/resource names receive lexical domain hints only; signature checks are evidence-only; control-flow edges are bounded references, not a complete decompilation", "imageBase": "0x50000000", "pointerCount": len(pointers), "pointers": pointers[:1024], "headerCandidates": header_candidates, "headers": headers, "functionMarkerStarts": discovered_function_starts[:4096], "functionCount": len(functions), "nativeTable": native_table, "nativeUsage": sorted(native_usage.values(), key=lambda item: (-item["count"], item["nativeIndex"])), "nativeDomainUsage": native_domain_usage, "domainUsage": domain_usage, "functionDomainUsage": function_domain_usage, "missionTrace": mission_trace, "signatureMismatchCount": signature_mismatches, "callReferences": call_references[:4096], "callGraph": call_graph, "callGraphEdgeCount": len(call_graph), "callGraphResolvedCallCount": sum(edge["count"] for edge in call_graph), "callGraphUnresolvedCallCount": unresolved_call_count, "functions": functions, "unresolved": unresolved}


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
    typed_records = build_typed_record_candidates(values)
    record_index = build_mission_record_index(typed_records, {})
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
        "typedRecordCandidates": typed_records,
        "typedRecordCount": len(typed_records),
        "missionRecordIndex": record_index,
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
    typed_records = build_typed_record_candidates(
        [{"offset": index, "value": element.get("tag")} for index, element in enumerate(elements)]
    )
    record_index = build_mission_record_index(typed_records, {})
    return {
        "format": "XML",
        "confidence": "verified XML element and attribute values",
        "root": root.tag,
        "elementCount": len(elements),
        "elements": elements,
        "typedRecordCandidates": typed_records,
        "typedRecordCount": len(typed_records),
        "missionRecordIndex": record_index,
    }


def rsc85_layout(data: bytes) -> dict[str, Any] | None:
    """Decode the RSC85 page counts needed to locate the object section.

    RSC85 flag fields are little-endian in the PC/Switch release.  The page
    counts select 512 KiB, 256 KiB, ... 4 KiB pages; the final catch-all group
    uses the smallest page size needed by the remaining virtual size.  This
    mirrors Magic-RDR's ``RSC85_PageSizesVirtual`` and ``RSC85_ObjectStart``
    logic without attempting to infer physical-page mappings.
    """
    if len(data) < 16 or data[:4] not in {b"RSC\x85", b"RSC\x86"}:
        return None
    flag1, flag2 = struct.unpack_from("<II", data, 8)
    virtual_counts = [
        (flag1 >> 14) & 0x3,
        (flag1 >> 8) & 0x3F,
        flag1 & 0xFF,
    ]
    physical_counts = [
        (flag1 >> 28) & 0x7,
        (flag1 >> 24) & 0xF,
        (flag1 >> 16) & 0xFF,
    ]
    object_start_page = (flag2 >> 28) & 0x7
    object_start_page_size = 4096 << object_start_page
    total_virtual = (flag2 & 0x3FFF) << 12
    total_physical = ((flag2 >> 14) & 0x3FFF) << 12

    pages: list[int] = []
    remaining = total_virtual
    page_size = 524288
    for group, count in enumerate((*virtual_counts, 2**31 - 1)):
        for _ in range(count):
            if remaining <= 0:
                break
            while page_size > remaining:
                page_size >>= 1
            pages.append(page_size)
            remaining -= page_size
        page_size >>= 1
        if remaining <= 0:
            break
    object_start = 0
    for size in pages:
        if size == object_start_page_size:
            break
        object_start += size
    else:
        object_start = 0
    return {
        "flag1": f"0x{flag1:08X}",
        "flag2": f"0x{flag2:08X}",
        "virtualPageCounts": virtual_counts,
        "physicalPageCounts": physical_counts,
        "objectStartPage": object_start_page,
        "objectStartPageSize": object_start_page_size,
        "totalVirtualBytes": total_virtual,
        "totalPhysicalBytes": total_physical,
        "virtualPageSizes": pages,
        "objectStart": object_start,
        "status": "validated-rsc85-virtual-page-layout",
    }


def expand_rsc85_payload(data: bytes) -> bytes | None:
    """Return the expanded RSC85 v2 virtual payload when it is available."""
    if data[:4] not in {b"RSC\x85", b"RSC\x86"} or len(data) < 16:
        return None
    version = struct.unpack_from("<I", data, 4)[0]
    if version != 2 or zstandard is None:
        return None
    header_length = 16
    transformed = decrypt_payload(data[header_length:])
    if transformed[:4] != b"\x28\xb5\x2f\xfd":
        return None
    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(transformed)) as reader:
        return reader.read()


def decode(data: bytes, native_names: dict[int, str] | None = None,
           native_metadata: dict[int, dict[str, Any]] | None = None,
           object_start: int | None = None) -> dict[str, Any]:
    if data[:4] == b"RBF0":
        return decode_rbf0(data)
    if data[:4] in RSC_MAGICS:
        version = struct.unpack_from("<I", data, 4)[0] if len(data) >= 8 else None
        header_length = 12 if data[4:5] in {b"\x05", b"\x06"} else 16
        payload = data[min(header_length, len(data)):]
        report: dict[str, Any] = {
            "format": "RSC",
            "confidence": "verified RSC wrapper; payload bytes preserved exactly",
            "magic": data[:4].decode("latin1"),
            "versionWord": version,
            "systemFlags": struct.unpack_from("<I", data, 8)[0] if len(data) >= 12 else None,
            "graphicsFlags": struct.unpack_from("<I", data, 12)[0] if len(data) >= 16 else None,
            "headerLength": header_length,
            "actualSize": len(data),
            "payloadOffset": header_length,
            "payloadSize": len(payload),
            "payloadSha256": hashlib.sha256(payload).hexdigest().upper(),
            "payloadPrefixHex": payload[:64].hex().upper(),
            "expectedExpandedSegments": expanded_segments(data, data[:4], header_length),
        }
        layout = rsc85_layout(data)
        if layout is not None:
            report["rsc85Layout"] = layout
        transformed_payload = payload
        # RSC85/RSC86 version 2 resources in the PC release apply the
        # established Magic-RDR AES-256-ECB transform sixteen times to each
        # complete block before the compression frame is exposed.  Keep the
        # original payload fields above so the package remains reproducible,
        # then record the transformed bytes used for semantic inspection.
        if data[:4] in {b"RSC\x85", b"RSC\x86"} and version == 2:
            try:
                transformed_payload = decrypt_payload(payload)
                report["payloadEncryption"] = {
                    "format": "AES-256-ECB",
                    "passes": 16,
                    "keyId": "Magic-RDR-AppGlobals.EncryptionKey",
                    "inputSize": len(payload),
                    "outputSize": len(transformed_payload),
                    "transformedPayloadSha256": hashlib.sha256(transformed_payload).hexdigest().upper(),
                    "transformedPayloadPrefixHex": transformed_payload[:64].hex().upper(),
                }
            except Exception as exc:
                report["decryptionError"] = str(exc)
        if zstandard is not None:
            try:
                with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(transformed_payload)) as reader:
                    expanded = reader.read()
                report["expandedSize"] = len(expanded)
                report["expandedSha256"] = hashlib.sha256(expanded).hexdigest().upper()
                report["compression"] = {
                    "format": "Zstandard",
                    "compressedSize": len(transformed_payload),
                    "expandedSize": len(expanded),
                }
                report["payload"] = decode(
                    expanded,
                    native_names,
                    native_metadata,
                    object_start=layout["objectStart"] if layout is not None else None,
                )
                return report
            except Exception as exc:
                report["decompressionError"] = str(exc)
        report["unresolved"] = ["RSC page/resource-graph compression or encryption", "mission VM header and opcode stream", "event timing, camera transforms, AI arguments, and audio codec fields"]
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
    vm = decode_vm(data, native_names=native_names, native_metadata=native_metadata, object_start=object_start)
    has_valid_script = any(item.get("valid") for item in vm.get("headers", []))
    resource_hints = resource_domain_report(identifiers)
    typed_records = build_typed_record_candidates(identifiers)
    trace_correlation = correlate_trace_to_records(vm.get("missionTrace", {}), typed_records)
    record_index = build_mission_record_index(typed_records, vm.get("missionTrace", {}))
    mission_trace = vm.get("missionTrace", {})
    if isinstance(mission_trace, dict):
        mission_trace = dict(mission_trace)
        mission_trace["resourceCorrelation"] = trace_correlation
        mission_trace["recordIndexSummary"] = {
            "recordCount": record_index.get("recordCount", 0),
            "recordsWithTraceReferences": record_index.get("recordsWithTraceReferences", 0),
        }
    return {
        "format": "raw",
        "confidence": "validated RDR resource-script header and bounded bytecode stream" if has_valid_script else "verified byte stream and printable identifiers only",
        "size": len(data),
        "strings": found, "semanticCounts": {"identifiers": len(identifiers), "animationIdentifiers": sum("_" in item["value"] and any(item["value"].lower().startswith(prefix) for prefix in ("air_", "grd_", "gent_", "gped_", "zomb_")) for item in identifiers)},
        "identifiers": identifiers,
        "resourceDomainHints": resource_hints,
        "missionDomainCorrelation": mission_domain_correlation(resource_hints, vm),
        "typedRecordCandidates": typed_records,
        "typedRecordCount": len(typed_records),
        "missionRecordIndex": record_index,
        "missionTrace": mission_trace,
        "missionTraceCorrelation": trace_correlation,
        "vmEvidence": {"imageBase": "0x50000000 (candidate)", "headerPointers": pointers, "wordOpcodeHistogram": dict(sorted(histogram.items(), key=lambda pair: (-pair[1], pair[0]))[:64]), "disassembly": vm},
        "unresolved": ["mission VM opcode semantics and typed arguments require an independently validated opcode table"]
    }


def rsc_segment_size(flags: int) -> int:
    """Decode the packed RSC page-count field into the expected segment size."""
    base_size = 0x200 << (flags & 0xF)
    pages = (
        ((flags >> 27) & 1)
        + (((flags >> 26) & 1) << 1)
        + (((flags >> 25) & 1) << 2)
        + (((flags >> 24) & 1) << 3)
        + (((flags >> 17) & 0x7F) << 4)
        + (((flags >> 11) & 0x3F) << 5)
        + (((flags >> 7) & 0xF) << 6)
        + (((flags >> 5) & 0x3) << 7)
        + (((flags >> 4) & 1) << 8)
    )
    return base_size * pages


def expanded_segments(data: bytes, magic: bytes, header_length: int) -> dict[str, Any]:
    """Return page-size evidence without applying RSC05 math to RSC85."""
    if magic in {b"RSC\x85", b"RSC\x86"}:
        return {
            "systemBytes": None,
            "graphicsBytes": None,
            "totalBytes": None,
            "status": "validated-rsc85-extended-flags; physical-page-mapping-unresolved",
            "formula": "RSC85/RSC86 virtual pages and object start are decoded from extended flags; physical page mapping is resource-specific",
        }
    system_bytes = rsc_segment_size(struct.unpack_from("<I", data, 8)[0]) if len(data) >= 12 else None
    graphics_bytes = rsc_segment_size(struct.unpack_from("<I", data, 12)[0]) if len(data) >= 16 else None
    return {
        "systemBytes": system_bytes,
        "graphicsBytes": graphics_bytes,
        "totalBytes": system_bytes + graphics_bytes if isinstance(system_bytes, int) and isinstance(graphics_bytes, int) else None,
        "status": "legacy-rsc05-rsc06-bitfield-formula",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resource", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--expanded-output", type=Path, help="Write the expanded RSC85 virtual payload when the AES/Zstandard path succeeds")
    parser.add_argument("--trace-output", type=Path, help="Write the bounded source-order mission trace as a separate JSON file")
    parser.add_argument("--records-output", type=Path, help="Write the bounded mission record index as a separate JSON file")
    parser.add_argument("--native-db", type=Path, help="Optional RDR1 native hash database JSON")
    args = parser.parse_args()
    native_metadata, native_path = load_native_database_metadata(args.native_db)
    native_names = {native_hash: item["name"] for native_hash, item in native_metadata.items() if item.get("name")}
    report = decode(args.resource.read_bytes(), native_names, native_metadata)
    if args.expanded_output:
        try:
            expanded = expand_rsc85_payload(args.resource.read_bytes())
            if expanded is None:
                report["expandedPayloadExport"] = {"status": "not-available"}
            else:
                args.expanded_output.parent.mkdir(parents=True, exist_ok=True)
                args.expanded_output.write_bytes(expanded)
                report["expandedPayloadExport"] = {
                    "status": "written",
                    "byteLength": len(expanded),
                    "sha256": hashlib.sha256(expanded).hexdigest().upper(),
                }
        except Exception as exc:
            report["expandedPayloadExport"] = {"status": "error", "error": str(exc)}
    native_report: dict[str, Any] = {"path": native_path, "count": len(native_metadata)}
    if native_path:
        try:
            if Path(native_path).stat().st_size > 128 * 1024 * 1024:
                native_report["status"] = "skipped-too-large-for-bounded-json-read"
        except OSError:
            native_report["status"] = "unavailable"
    report["nativeDatabase"] = native_report
    report["source"] = str(args.resource)
    if args.trace_output:
        trace = report.get("missionTrace")
        if not isinstance(trace, dict) and isinstance(report.get("payload"), dict):
            trace = report["payload"].get("missionTrace")
        if isinstance(trace, dict) and trace.get("format") == "RDR mission trace":
            args.trace_output.parent.mkdir(parents=True, exist_ok=True)
            args.trace_output.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            report["missionTraceExport"] = {"status": "written", "file": str(args.trace_output), "eventCount": trace.get("eventCount", 0)}
        else:
            report["missionTraceExport"] = {"status": "not-available", "reason": "no validated VM trace was produced"}
    if args.records_output:
        record_index = report.get("missionRecordIndex")
        if not isinstance(record_index, dict) and isinstance(report.get("payload"), dict):
            record_index = report["payload"].get("missionRecordIndex")
        if isinstance(record_index, dict) and record_index.get("format") == "RDR mission record index":
            args.records_output.parent.mkdir(parents=True, exist_ok=True)
            args.records_output.write_text(json.dumps(record_index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            report["missionRecordExport"] = {"status": "written", "file": str(args.records_output), "recordCount": record_index.get("recordCount", 0)}
        else:
            report["missionRecordExport"] = {"status": "not-available", "reason": "no bounded mission record index was produced"}
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
