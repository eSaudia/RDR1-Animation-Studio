#!/usr/bin/env python3
"""Build a bounded VM/header index from extracted mission resources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from resource_semantics import decode, load_native_database_metadata


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
            header_count += len(valid_headers)
            function_count += len(functions)
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
                "functionCount": len(functions),
                "identifiers": [entry.get("value") for entry in payload.get("identifiers", [])[:64]] if isinstance(payload, dict) else [],
                "unresolved": disassembly.get("unresolved", []) if isinstance(disassembly, dict) else payload.get("unresolved", []) if isinstance(payload, dict) else [],
            })
        except Exception as exc:
            resources.append({"name": item.get("name"), "archive": item.get("archive"), "internalPath": item.get("internalPath"), "entryIndex": item.get("entryIndex"), "error": str(exc)})
    result = {
        "format": "RDR mission VM index",
        "confidence": "header-bearing resources are validated; all other entries remain evidence-only",
        "resourceCount": len(resources),
        "validHeaderCount": header_count,
        "functionCount": function_count,
        "nativeDatabase": {"path": native_path, "count": len(native_metadata)},
        "resources": resources,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
