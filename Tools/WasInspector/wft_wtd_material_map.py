#!/usr/bin/env python3
"""Create a conservative WFT shader-texture to WTD-entry mapping report."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("geometry",type=Path); p.add_argument("textures",type=Path); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    geometry=json.loads(a.geometry.read_text(encoding="utf-8")); textures=json.loads(a.textures.read_text(encoding="utf-8"))
    entries=textures.get("textures",[]); names=[]
    for g in geometry.get("geometries",[]):
        for name in g.get("shader_textures",[]):
            if name and name not in names: names.append(name)
    mappings=[]
    for index,name in enumerate(names):
        entry=entries[index] if index < len(entries) else None
        mappings.append({"shaderTexture":name,"textureIndex":index if entry else None,"texture":entry,"confidence":"order-correlated-evidence" if entry else "unresolved"})
    report={"format":"RDR1 WFT/WTD material mapping","geometry":str(a.geometry),"textures":str(a.textures),"shaderTextureCount":len(names),"textureEntryCount":len(entries),"mappings":mappings,"unresolved":["WTD dictionary name/hash semantics and shader slot semantics require further validation"]}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"shaderTextureCount":len(names),"mapped":sum(1 for x in mappings if x["texture"] is not None)})); return 0
if __name__ == "__main__": raise SystemExit(main())
