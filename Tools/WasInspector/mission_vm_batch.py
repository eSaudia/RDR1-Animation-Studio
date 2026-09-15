#!/usr/bin/env python3
"""Build a bounded VM/header index from extracted mission resources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from resource_semantics import MISSION_DOMAINS, decode, load_native_database_metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON array of extracted resource records")
    parser.add_argument("output", type=Path)
    parser.add_argument("--native-db", type=Path, help="Optional RDR1 native hash database JSON")
    args = parser.parse_args()
    native_metadata, native_path = load_native_database_metadata(args.native_db)
    native_names = {native_hash: item["name"] for native_hash, item in native_metadata.items() if item.get("name")}
    records = json.loads(args.manifest.read_text(encoding="utf-8"))
    resources: list[dict[str, Any]] = []
    header_count = 0
    function_count = 0
    call_reference_count = 0
    call_graph_edge_count = 0
    call_graph_resolved_call_count = 0
    call_graph_unresolved_call_count = 0
    signature_mismatch_count = 0
    native_usage: dict[str, dict[str, Any]] = {}
    native_domain_totals: dict[str, dict[str, Any]] = {}
    domain_usage = {domain: {"callCount": 0, "nativeCount": 0} for domain in MISSION_DOMAINS}
    resource_domain_totals = {domain: {"resourceCount": 0, "uniqueIdentifierCount": 0} for domain in MISSION_DOMAINS}
    typed_record_totals = {domain: {"candidateCount": 0, "resourceCount": 0} for domain in MISSION_DOMAINS}
    trace_correlation_totals = {"matchedDomainResourceCount": 0, "resourcesWithTraceDomainEvidence": 0, "resourcesWithCandidates": 0}
    record_index_totals = {"recordCount": 0, "recordsWithTraceReferences": 0, "resourcesWithRecordIndex": 0}
    for item in records:
        path = Path(item["file"])
        try:
            report = decode(path.read_bytes(), native_names, native_metadata)
            payload = report.get("payload", report)
            evidence = payload.get("vmEvidence", {}) if isinstance(payload, dict) else {}
            disassembly = evidence.get("disassembly", {}) if isinstance(evidence, dict) else {}
            headers = disassembly.get("headers", []) if isinstance(disassembly, dict) else []
            valid_headers = [header for header in headers if header.get("valid")]
            functions = disassembly.get("functions", []) if isinstance(disassembly, dict) else []
            call_references = disassembly.get("callReferences", []) if isinstance(disassembly, dict) else []
            call_graph = disassembly.get("callGraph", []) if isinstance(disassembly, dict) else []
            usage = disassembly.get("nativeUsage", []) if isinstance(disassembly, dict) else []
            domain_entries = disassembly.get("nativeDomainUsage", []) if isinstance(disassembly, dict) else []
            per_domain_usage = disassembly.get("domainUsage", {}) if isinstance(disassembly, dict) else {}
            header_count += len(valid_headers)
            function_count += len(functions)
            call_reference_count += len(call_references)
            call_graph_edge_count += int(disassembly.get("callGraphEdgeCount", len(call_graph)) or 0) if isinstance(disassembly, dict) else 0
            call_graph_resolved_call_count += int(disassembly.get("callGraphResolvedCallCount", 0) or 0) if isinstance(disassembly, dict) else 0
            call_graph_unresolved_call_count += int(disassembly.get("callGraphUnresolvedCallCount", 0) or 0) if isinstance(disassembly, dict) else 0
            signature_mismatch_count += int(disassembly.get("signatureMismatchCount", 0) or 0) if isinstance(disassembly, dict) else 0
            for domain in MISSION_DOMAINS:
                item_domain = per_domain_usage.get(domain, {}) if isinstance(per_domain_usage, dict) else {}
                domain_usage[domain]["callCount"] += int(item_domain.get("callCount", 0) or 0) if isinstance(item_domain, dict) else 0
                domain_usage[domain]["nativeCount"] += int(item_domain.get("nativeCount", 0) or 0) if isinstance(item_domain, dict) else 0
            for entry in domain_entries:
                if not isinstance(entry, dict) or not entry.get("hash"):
                    continue
                key = str(entry["hash"])
                total = native_domain_totals.setdefault(key, {"hash": key, "count": 0, "domains": []})
                total["count"] += int(entry.get("count", 0) or 0)
                total["domains"] = sorted(set(total["domains"]) | set(entry.get("domains", [])))
                for field in ("nativeIndex", "name", "evidence"):
                    if field in entry and field not in total:
                        total[field] = entry[field]
            resource_hints = payload.get("resourceDomainHints", {}) if isinstance(payload, dict) else {}
            if isinstance(resource_hints, dict):
                for domain in MISSION_DOMAINS:
                    hint = resource_hints.get(domain, {})
                    if not isinstance(hint, dict) or not hint:
                        continue
                    resource_domain_totals[domain]["resourceCount"] += 1
                    resource_domain_totals[domain]["uniqueIdentifierCount"] += int(hint.get("uniqueIdentifierCount", 0) or 0)
            typed_candidates = payload.get("typedRecordCandidates", []) if isinstance(payload, dict) else []
            if isinstance(typed_candidates, list) and typed_candidates:
                trace_correlation_totals["resourcesWithCandidates"] += 1
                candidate_domains: set[str] = set()
                for candidate in typed_candidates[:4096]:
                    if not isinstance(candidate, dict):
                        continue
                    for domain in candidate.get("domains", []) if isinstance(candidate.get("domains"), list) else []:
                        if domain in typed_record_totals:
                            typed_record_totals[domain]["candidateCount"] += 1
                            candidate_domains.add(domain)
                for domain in candidate_domains:
                    typed_record_totals[domain]["resourceCount"] += 1
            trace_correlation = payload.get("missionTraceCorrelation", {}) if isinstance(payload, dict) else {}
            if isinstance(trace_correlation, dict):
                if trace_correlation.get("matchedDomains"):
                    trace_correlation_totals["matchedDomainResourceCount"] += 1
                if trace_correlation.get("status") in {"matched-domain-evidence", "trace-domain-evidence-without-resource-candidate"}:
                    trace_correlation_totals["resourcesWithTraceDomainEvidence"] += 1
            record_index = payload.get("missionRecordIndex", {}) if isinstance(payload, dict) else {}
            if isinstance(record_index, dict) and record_index:
                record_index_totals["resourcesWithRecordIndex"] += 1
                record_index_totals["recordCount"] += int(record_index.get("recordCount", 0) or 0)
                record_index_totals["recordsWithTraceReferences"] += int(record_index.get("recordsWithTraceReferences", 0) or 0)
            for entry in usage:
                if not isinstance(entry, dict) or not entry.get("hash"):
                    continue
                key = str(entry["hash"])
                total = native_usage.setdefault(key, {"hash": key, "count": 0})
                total["count"] += int(entry.get("count", 0) or 0)
                total["signatureMismatches"] = total.get("signatureMismatches", 0) + int(entry.get("signatureMismatches", 0) or 0)
                for field in ("nativeIndex", "name"):
                    if field in entry and field not in total:
                        total[field] = entry[field]
            resources.append({
                "name": item.get("name"),
                "archive": item.get("archive"),
                "internalPath": item.get("internalPath"),
                "entryIndex": item.get("entryIndex"),
                "size": path.stat().st_size,
                "format": report.get("format"),
                "expandedFormat": payload.get("format") if isinstance(payload, dict) else None,
                "headerCandidates": disassembly.get("headerCandidates", []) if isinstance(disassembly, dict) else [],
                "headers": valid_headers,
                "nativeTable": disassembly.get("nativeTable", [])[:4096] if isinstance(disassembly, dict) else [],
                "nativeUsage": usage[:4096] if isinstance(usage, list) else [],
                "nativeDomainUsage": domain_entries[:4096] if isinstance(domain_entries, list) else [],
                "resourceDomainHints": payload.get("resourceDomainHints", {}) if isinstance(payload, dict) else {},
                "missionDomainCorrelation": payload.get("missionDomainCorrelation", {}) if isinstance(payload, dict) else {},
                "typedRecordCandidates": typed_candidates[:4096] if isinstance(typed_candidates, list) else [],
                "typedRecordCount": payload.get("typedRecordCount", len(typed_candidates)) if isinstance(payload, dict) and isinstance(typed_candidates, list) else 0,
                "missionTraceCorrelation": trace_correlation,
                "missionRecordIndex": record_index,
                "callReferences": call_references[:4096] if isinstance(call_references, list) else [],
                "callGraph": call_graph[:4096] if isinstance(call_graph, list) else [],
                "callGraphEdgeCount": disassembly.get("callGraphEdgeCount", len(call_graph)) if isinstance(disassembly, dict) else 0,
                "callGraphResolvedCallCount": disassembly.get("callGraphResolvedCallCount", 0) if isinstance(disassembly, dict) else 0,
                "callGraphUnresolvedCallCount": disassembly.get("callGraphUnresolvedCallCount", 0) if isinstance(disassembly, dict) else 0,
                "signatureMismatchCount": disassembly.get("signatureMismatchCount", 0) if isinstance(disassembly, dict) else 0,
                "functionCount": len(functions),
                "identifiers": [entry.get("value") for entry in payload.get("identifiers", [])[:64]] if isinstance(payload, dict) else [],
                "unresolved": disassembly.get("unresolved", []) if isinstance(disassembly, dict) else payload.get("unresolved", []) if isinstance(payload, dict) else [],
            })
        except Exception as exc:
            resources.append({"name": item.get("name"), "archive": item.get("archive"), "internalPath": item.get("internalPath"), "entryIndex": item.get("entryIndex"), "error": str(exc)})
    native_report: dict[str, Any] = {"path": native_path, "count": len(native_metadata)}
    if native_path:
        try:
            if Path(native_path).stat().st_size > 128 * 1024 * 1024:
                native_report["status"] = "skipped-too-large-for-bounded-json-read"
        except OSError:
            native_report["status"] = "unavailable"
    result = {
        "format": "RDR mission VM index",
        "confidence": "header-bearing resources and bounded internal call graphs are validated; native/resource domain labels are lexical evidence; all other entries remain evidence-only",
        "resourceCount": len(resources),
        "validHeaderCount": header_count,
        "functionCount": function_count,
        "callReferenceCount": call_reference_count,
        "callGraphEdgeCount": call_graph_edge_count,
        "callGraphResolvedCallCount": call_graph_resolved_call_count,
        "callGraphUnresolvedCallCount": call_graph_unresolved_call_count,
        "signatureMismatchCount": signature_mismatch_count,
        "domainUsage": domain_usage,
        "resourceDomainHints": resource_domain_totals,
        "typedRecordCandidates": typed_record_totals,
        "missionTraceCorrelation": trace_correlation_totals,
        "missionRecordIndex": record_index_totals,
        "nativeUsage": sorted(native_usage.values(), key=lambda item: (-item["count"], item["hash"])),
        "nativeDomainUsage": sorted(native_domain_totals.values(), key=lambda item: (-item["count"], item["hash"])),
        "nativeDatabase": native_report,
        "resources": resources,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
