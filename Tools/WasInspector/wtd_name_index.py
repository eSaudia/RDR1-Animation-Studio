#!/usr/bin/env python3
"""Build a content-based DDS name index from WTD inspector reports."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("reports",type=Path); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    index={}; report_count=0
    for path in a.reports.rglob("*.wtd.json"):
        try: report=json.loads(path.read_text(encoding="utf-8"))
        except Exception: continue
        report_count += 1
        for name in report.get("embeddedTextureNames",[]): index.setdefault(name.lower(),[]).append({"report":str(path),"source":report.get("source"),"sha256":report.get("sha256")})
    result={"format":"RDR1 WTD content texture index","reportCount":report_count,"textureNameCount":len(index),"entries":index,"unresolved":["hash-only texture references are not included"]}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"reports":report_count,"textureNames":len(index)})); return 0
if __name__ == "__main__": raise SystemExit(main())
