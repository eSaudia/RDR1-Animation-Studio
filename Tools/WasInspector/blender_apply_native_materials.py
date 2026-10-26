"""Apply extracted native DDS textures to mesh materials in a Blender scene."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import bpy

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--blend",type=Path,required=True); p.add_argument("--texture-dir",type=Path,required=True); p.add_argument("--output",type=Path,required=True)
    argv=sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else sys.argv[1:]; a=p.parse_args(argv)
    bpy.ops.wm.open_mainfile(filepath=str(a.blend.resolve()))
    textures=[]
    # Prefer decoded MagicRDR previews when an extracted compressed DDS has a
    # sibling TGA/PNG.  The DDS is the original console byte stream and Blender
    # interprets its Xbox byte order as the dark red/black static pattern.
    paths = sorted(list(a.texture_dir.glob("*.tga")) + list(a.texture_dir.glob("*.png")))
    decoded_stems = {p.stem for p in paths}
    paths += [p for p in sorted(a.texture_dir.glob("*.dds")) if p.stem not in decoded_stems]
    for path in paths:
        try: textures.append(bpy.data.images.load(str(path.resolve()), check_existing=True))
        except Exception: pass
    # Match recovered atlases by their semantic filename instead of sorted
    # dictionary order. WTD extraction order is not shader-slot order.
    by_name = {str(image.filepath).lower(): image for image in textures}

    def choose_texture(material_name: str, semantic: str):
        name = material_name.lower()
        candidates = list(by_name.items())
        if semantic == "normal":
            candidates = [(p, i) for p, i in candidates if "_n1" in p or "wrinkle" in p or "normal" in p]
        elif semantic == "roughness":
            candidates = [(p, i) for p, i in candidates if "_so1" in p or "spec" in p or "so." in p or "rough" in p]
        elif "hand" in name:
            candidates = [(p, i) for p, i in candidates if "hand" in p]
        elif "player_jack" in name or "cowboy" in name:
            candidates = [(p, i) for p, i in candidates if "john_out" in p]
        else:
            # Leather/cloth shader slots are layered over the selected outfit.
            # Do not use player_default_alphakill_d1 as their color: that file is
            # a cutout/body atlas and renders as the red/white static pattern.
            candidates = [(p, i) for p, i in candidates if "john_out" in p]
            if not candidates:
                candidates = [(p, i) for p, i in by_name.items() if "player_default" in p and "alphakill" not in p]
        if semantic == "diffuse":
            # Diffuse/base-color must never fall back to a normal, specular,
            # mask, or wrinkle atlas. Those maps are commonly red/black and
            # produce the exact dark glossy appearance seen in Blender.
            candidates = [(p, i) for p, i in candidates
                          if not any(tag in p for tag in ("_n1", "_so1", "wrinkle", "_m1", "_mask", "normal", "rough", "spec", "so."))]
        if candidates:
            return candidates[0][1]
        if semantic == "diffuse":
            safe = [(p, i) for p, i in by_name.items()
                    if not any(tag in p for tag in ("_n1", "_so1", "wrinkle", "_m1", "_mask", "normal", "rough", "spec", "so."))]
            return safe[0][1] if safe else None
        return textures[0] if textures else None

    applied=0
    for obj in bpy.data.objects:
        if obj.type != "MESH": continue
        for slot_index, slot in enumerate(obj.material_slots):
            mat=slot.material
            if mat is None: mat=bpy.data.materials.new(f"RDR1 Native Material {slot_index}"); slot.material=mat
            mat.use_nodes=True; nodes=mat.node_tree.nodes; links=mat.node_tree.links
            bsdf=next((n for n in nodes if n.type=="BSDF_PRINCIPLED"), None)
            if bsdf is None: continue
            material_name = mat.name.lower()
            semantic = "normal" if material_name.endswith("_n1.dds") or "_n1" in material_name else ("roughness" if "_so1" in material_name else "diffuse")
            image=choose_texture(mat.name, semantic)
            if image is None: continue
            tex_name = "RDR1 Native Normal" if semantic == "normal" else ("RDR1 Native Roughness" if semantic == "roughness" else "RDR1 Native Diffuse")
            tex=nodes.get(tex_name) or nodes.new("ShaderNodeTexImage"); tex.name=tex_name; tex.image=image
            try:
                image.colorspace_settings.name = "Non-Color" if semantic in ("normal", "roughness") else "sRGB"
            except Exception: pass
            if semantic == "normal":
                for link in list(links):
                    if link.to_node is bsdf and link.to_socket.name == "Normal": links.remove(link)
                normal=nodes.get("RDR1 Native Normal Map") or nodes.new("ShaderNodeNormalMap"); normal.name="RDR1 Native Normal Map"
                if not any(link.to_node is normal and link.to_socket.name=="Color" for link in links): links.new(tex.outputs.get("Color"), normal.inputs.get("Color"))
                if not any(link.to_node is bsdf and link.to_socket.name=="Normal" for link in links): links.new(normal.outputs.get("Normal"), bsdf.inputs.get("Normal"))
            elif semantic == "roughness":
                for link in list(links):
                    if link.to_node is bsdf and link.to_socket.name == "Roughness": links.remove(link)
                links.new(tex.outputs.get("Color"), bsdf.inputs.get("Roughness"))
            else:
                for link in list(links):
                    if link.to_node is bsdf and link.to_socket.name == "Base Color": links.remove(link)
                links.new(tex.outputs.get("Color"), bsdf.inputs.get("Base Color"))
                # alphaKill/alphaCrop materials use the diffuse texture's alpha, not
                # the mask atlas as base color.
                # The outfit atlas is DXT5 but its alpha is packed shader data,
                # not mesh transparency.  Only dedicated alphaKill/alphaCrop
                # maps should drive the Principled Alpha socket.
                image_name = image.filepath.lower()
                if ("alphakill" in material_name or "alphacrop" in material_name) and ("alphakill" in image_name or "alphacrop" in image_name) and image_name.endswith((".dds", ".png")):
                    alpha_socket = tex.outputs.get("Alpha")
                    if alpha_socket and bsdf.inputs.get("Alpha") and not any(link.to_node is bsdf and link.to_socket.name=="Alpha" for link in links): links.new(alpha_socket, bsdf.inputs.get("Alpha"))
                    try: mat.surface_render_method = 'DITHERED'
                    except Exception:
                        try: mat.blend_method = 'HASHED'
                        except Exception: pass
            mat["rdr1_native_texture"] = str(image.filepath); mat["rdr1_material_channel"] = semantic; mat["rdr1_material_status"]="native DDS channel mapping applied"; applied += 1
    a.output.parent.mkdir(parents=True,exist_ok=True); bpy.ops.wm.save_as_mainfile(filepath=str(a.output.resolve()))
    report={"format":"RDR1 native Blender material application","blend":str(a.blend),"textureDirectory":str(a.texture_dir),"loadedTextures":len(textures),"appliedSlots":applied,"channels":["diffuse/base color","normal map","specular/roughness","alpha cutout"]}
    a.output.with_suffix(".native-materials.json").write_text(json.dumps(report,indent=2),encoding="utf-8"); print(json.dumps(report)); return 0
if __name__ == "__main__": raise SystemExit(main())
