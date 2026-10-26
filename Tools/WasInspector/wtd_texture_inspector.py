#!/usr/bin/env python3
"""Inventory native RDR1 WTD texture payloads without CodeX.

This is deliberately evidence-first: it validates DDS headers embedded in the
resource stream and records offsets/sizes, but does not guess shader semantics
or silently convert compressed console texture formats.
"""
from __future__ import annotations
import argparse, hashlib, json, struct
import re
from pathlib import Path
from rsc_resource_unpack import unpack

def make_dds_header(width: int, height: int, fourcc: str) -> bytes:
    """Build a minimal legacy DDS header for RAGE's headerless XTD payload."""
    code = fourcc.encode("ascii")[:4].ljust(4, b" ")
    # DDS_HEADER + DDS_PIXELFORMAT (DXT1/DXT5/ATI2/BC4/BC5).
    header = bytearray(b"DDS ") + bytearray(124)
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, 8, 0x00081007)  # CAPS|HEIGHT|WIDTH|PIXELFORMAT|LINEARSIZE
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    block = {"DXT1": 8, "DXT3": 16, "DXT5": 16, "ATI2": 16, "BC4U": 8, "BC5U": 16}.get(fourcc, 16)
    struct.pack_into("<I", header, 20, max(1, ((width + 3) // 4) * ((height + 3) // 4) * block))
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, 0x00000004)  # DDPF_FOURCC
    header[84:88] = code
    struct.pack_into("<I", header, 108, 0x1000)  # DDSCAPS_TEXTURE
    return bytes(header)

def make_rgba_dds_header(width: int, height: int) -> bytes:
    """Legacy uncompressed RGBA DDS header for decoded RDR1 console textures."""
    header = bytearray(b"DDS ") + bytearray(124)
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, 8, 0x0000100F)  # CAPS|HEIGHT|WIDTH|PITCH|PIXELFORMAT
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, width * 4)
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, 0x00000041)  # RGB|ALPHAPIXELS
    struct.pack_into("<I", header, 84, 32)
    # MagicRDR writes A8R8G8B8 masks (the byte stream is BGRA).
    struct.pack_into("<I", header, 88, 0x00FF0000)
    struct.pack_into("<I", header, 92, 0x0000FF00)
    struct.pack_into("<I", header, 96, 0x000000FF)
    struct.pack_into("<I", header, 100, 0xFF000000)
    struct.pack_into("<I", header, 108, 0x1000)
    return bytes(header)

def decode_rdr1_dxt5(data: bytes, width: int, height: int) -> bytes:
    """Decode the PC/Xbox RDR1 DXT5 ordering used by MagicRDR."""
    out = bytearray(width * height * 4)
    xb, yb = width // 4, height // 4
    for y in range(yb):
        for x in range(xb):
            p = (y * xb + x) * 16
            a0, a1 = data[p + 1], data[p]
            mask = 0
            for off in (6, 7, 4, 5, 2, 3): mask = (mask << 8) | data[p + off]
            alpha = [a0, a1]
            if a0 > a1:
                alpha += [(6*a0+a1+3)//7, (5*a0+2*a1+3)//7,
                          (4*a0+3*a1+3)//7, (3*a0+4*a1+3)//7,
                          (2*a0+5*a1+3)//7, (a0+6*a1+3)//7]
            else:
                alpha += [(4*a0+a1+2)//5, (3*a0+2*a1+2)//5,
                          (2*a0+3*a1+2)//5, (a0+4*a1+2)//5, 0, 255]
            c0 = (data[p+8] << 8) | data[p+9]
            c1 = (data[p+10] << 8) | data[p+11]
            code = struct.unpack_from("<I", data, p + 12)[0]
            def rgb(c): return ((8*(c & 31)), (4*((c >> 5) & 63)), (8*((c >> 11) & 31)))
            r0,g0,b0 = rgb(c0); r1,g1,b1 = rgb(c1)
            for k in range(4):
                row = k ^ 1
                for i in range(4):
                    ai = alpha[mask & 7]; mask >>= 3
                    ci = code & 3; code >>= 2
                    if ci == 0: rgbv = (r0,g0,b0)
                    elif ci == 1: rgbv = (r1,g1,b1)
                    elif ci == 2: rgbv = ((2*r0+r1)//3, (2*g0+g1)//3, (2*b0+b1)//3) if c0 > c1 else ((r0+r1)//2, (g0+g1)//2, (b0+b1)//2)
                    else: rgbv = ((r0+2*r1)//3, (g0+2*g1)//3, (b0+2*b1)//3) if c0 > c1 else (0,0,0)
                    q = ((y*4 + row) * width + x*4 + i) * 4
                    out[q:q+4] = bytes((*rgbv, ai))
    return bytes(out)

def write_tga(path: Path, rgba: bytes, width: int, height: int) -> None:
    # TGA is deliberately used for decoded output: it is lossless, simple,
    # and avoids platform-specific DDS header interpretation in Blender.
    bgra = bytearray(len(rgba))
    for i in range(0, len(rgba), 4):
        bgra[i:i+4] = bytes((rgba[i+2], rgba[i+1], rgba[i], rgba[i+3]))
    header = bytearray(18)
    header[2] = 2
    struct.pack_into("<HH", header, 12, width, height)
    header[16] = 32
    header[17] = 0x28  # top-left origin + 8-bit alpha
    path.write_bytes(header + bgra)

def _xg_address_x(offset: int, width: int, pitch: int) -> int:
    aligned = (width + 31) & ~31
    log_bpp = (pitch >> 2) + ((pitch >> 1) >> (pitch >> 2))
    offset_b = offset << log_bpp
    offset_t = ((offset_b & ~4095) >> 3) + ((offset_b & 1792) >> 2) + (offset_b & 63)
    offset_m = offset_t >> (7 + log_bpp)
    macro_x = (offset_m % (aligned >> 5)) << 2
    tile = (((offset_t >> (5 + log_bpp)) & 2) + (offset_b >> 6)) & 3
    macro = (macro_x + tile) << 3
    micro = ((((offset_t >> 1) & ~15) + (offset_t & 15)) & ((pitch << 3) - 1)) >> log_bpp
    return macro + micro

def _xg_address_y(offset: int, width: int, pitch: int) -> int:
    aligned = (width + 31) & ~31
    log_bpp = (pitch >> 2) + ((pitch >> 1) >> (pitch >> 2))
    offset_b = offset << log_bpp
    offset_t = ((offset_b & ~4095) >> 3) + ((offset_b & 1792) >> 2) + (offset_b & 63)
    offset_m = offset_t >> (7 + log_bpp)
    macro_y = (offset_m // (aligned >> 5)) << 2
    tile = ((offset_t >> (6 + log_bpp)) & 1) + ((offset_b & 2048) >> 10)
    macro = (macro_y + tile) << 3
    micro = ((((offset_t & (((pitch << 6) - 1) & ~31)) + ((offset_t & 15) << 1)) >> (3 + log_bpp)) & ~1)
    return macro + micro + ((offset_t & 16) >> 4)

def untile_xbox360(data: bytes, width: int, height: int, fourcc: str) -> bytes:
    """Convert RAGE/Xenon tiled DXT blocks to linear PC DDS block order."""
    block_bytes = {"DXT1": 8, "DXT3": 16, "DXT5": 16, "ATI2": 16, "BC4U": 8, "BC5U": 16}[fourcc]
    bw, bh = max(1, (width + 3) // 4), max(1, (height + 3) // 4)
    linear = bytearray(len(data))
    for y in range(bh):
        for x in range(bw):
            block = y * bw + x
            tx, ty = _xg_address_x(block, bw, block_bytes), _xg_address_y(block, bw, block_bytes)
            src, dst = block * block_bytes, (ty * bw + tx) * block_bytes
            if src + block_bytes <= len(data) and dst + block_bytes <= len(linear):
                linear[dst:dst + block_bytes] = data[src:src + block_bytes]
    # Xenon stores compressed words in PowerPC byte order.
    for i in range(0, len(linear) - 1, 2):
        linear[i], linear[i + 1] = linear[i + 1], linear[i]
    return bytes(linear)

def linearize_rdr1_xbox(data: bytes, width: int, height: int, fourcc: str) -> bytes:
    """MagicRDR ConvertToLinearTexture: tiled blocks to linear, no endian swap."""
    block_bytes = {"DXT1":8,"DXT3":16,"DXT5":16,"ATI2":16,"BC4U":8,"BC5U":16}[fourcc]
    bw, bh = max(1, (width + 3) // 4), max(1, (height + 3) // 4)
    linear = bytearray(len(data))
    for y in range(bh):
        for x in range(bw):
            block = y * bw + x
            tx, ty = _xg_address_x(block, bw, block_bytes), _xg_address_y(block, bw, block_bytes)
            src, dst = block * block_bytes, (ty * bw + tx) * block_bytes
            if src + block_bytes <= len(data) and dst + block_bytes <= len(linear):
                linear[dst:dst + block_bytes] = data[src:src + block_bytes]
    return bytes(linear)

def inspect_headerless_xtd(data: bytes, extract_dir: Path | None, names: list[str]) -> list[dict]:
    """Recover the common PC WTD/XTD record: packed dimensions + fourcc,
    followed by a page-aligned block-compressed image without a DDS header."""
    results=[]
    for match in re.finditer(rb"DXT1|DXT3|DXT5|ATI2|BC4U|BC5U", data):
        pos=match.start(); fourcc=match.group().decode("ascii")
        if pos < 4: continue
        packed=struct.unpack_from("<I", data, pos-4)[0]
        width, height = packed & 0xffff, (packed >> 16) & 0xffff
        if width < 4 or height < 4 or width > 16384 or height > 16384: continue
        block={"DXT1":8,"DXT3":16,"DXT5":16,"ATI2":16,"BC4U":8,"BC5U":16}[fourcc]
        size=max(1,((width+3)//4)*((height+3)//4)*block)
        payload=((pos+4095)//4096)*4096
        if payload+size > len(data): continue
        # Avoid treating the same payload as multiple records.
        if any(x["offset"] == payload for x in results): continue
        item={"index":len(results),"offset":payload,"size":size,"width":width,"height":height,"format":fourcc,"headerReconstructed":True}
        if extract_dir is not None:
            extract_dir.mkdir(parents=True,exist_ok=True)
            label=Path(names[0]).stem if names else f"texture_{len(results):04d}"
            out=extract_dir/f"{label}_{len(results):04d}_{width}x{height}.dds"
            # RDR1 PC WTD resources expose this stream already in linear block
            # order. Applying the Xenon untile transform here produces the
            # static/checkerboard corruption seen in Blender; retain the raw
            # linear payload and only reconstruct the DDS header.
            payload_data = data[payload:payload+size]
            if fourcc == "DXT5":
                # ReadTextureInfo in MagicRDR linearizes Xbox blocks before its
                # custom DXT5 decoder.  This is separate from SaveDDS, which
                # additionally swaps 16-bit words for a PC DDS file.
                rgba = decode_rdr1_dxt5(linearize_rdr1_xbox(payload_data, width, height, fourcc), width, height)
                tga = out.with_suffix(".tga")
                write_tga(tga, rgba, width, height)
                out.write_bytes(make_dds_header(width, height, fourcc) + payload_data)
                item["decodedFile"] = str(tga)
                item["rdr1MagicRdrDxtDecoded"] = True
            else:
                out.write_bytes(make_dds_header(width,height,fourcc) + payload_data)
            item["file"]=str(out); item["rdr1LinearPayload"] = True
        results.append(item)
    return results

def dictionary_metadata(data: bytes) -> dict:
    """Read the small XTD dictionary header/records used by MagicRDR.

    This metadata pass is intentionally independent of payload decoding. It
    prevents the report from presenting a heuristic DXT marker as the texture
    name and records the dimensions/format declared by the WTD itself.
    """
    if len(data) < 0x20:
        return {}
    u32 = lambda o: struct.unpack_from("<I", data, o)[0]
    def ptr(v: int) -> int:
        return v & 0x0FFFFFFF if (v >> 28) == 5 else v
    try:
        hash_ptr, count, list_ptr = ptr(u32(0x10)), struct.unpack_from("<H", data, 0x14)[0], ptr(u32(0x18))
        if not (0 < count <= 4096 and 0 <= hash_ptr < len(data) and 0 <= list_ptr < len(data)):
            return {}
        records=[]
        for i in range(count):
            po = list_ptr + i * 4
            if po + 4 > len(data): break
            rec = ptr(u32(po))
            if rec + 0x28 > len(data): continue
            name_ptr = ptr(u32(rec + 0x18))
            end = data.find(b"\x00", name_ptr, min(len(data), name_ptr + 512)) if name_ptr < len(data) else -1
            name = data[name_ptr:end].decode("ascii", "replace") if end > name_ptr else ""
            width, height = struct.unpack_from("<HH", data, rec + 0x20)
            records.append({"recordOffset": rec, "name": name, "width": width, "height": height})
        return {"dictionaryTextureCount": len(records), "dictionaryTextures": records}
    except (struct.error, ValueError):
        return {}

def dds_size(data: bytes, offset: int) -> int:
    if offset + 128 > len(data) or data[offset:offset+4] != b"DDS ": return 0
    height, width, pitch, _, mip = struct.unpack_from("<5I", data, offset + 12)
    fourcc = data[offset + 84:offset + 88]
    block = {b"DXT1": 8, b"DXT3": 16, b"DXT5": 16, b"ATI2": 16, b"BC4U": 8, b"BC5U": 16}.get(fourcc)
    levels = max(1, mip)
    total = 128
    w, h = max(1, width), max(1, height)
    for _ in range(levels):
        total += max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block if block else max(1, w * h * 4)
        w, h = max(1, w // 2), max(1, h // 2)
    return min(total, len(data) - offset)

def inspect(path: Path, extract_dir: Path | None = None) -> dict:
    raw = path.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    try: data = unpack(raw) if raw[:4] in {b"RSC\x05", b"RSC\x06", b"RSC\x85", b"RSC\x86"} else raw
    except Exception as exc: return {"format":"RDR1 WTD texture inventory", "source":str(path), "status":"decode-failed", "error":str(exc), "textures":[]}
    textures=[]; cursor=0
    embedded_names=[]
    for match in re.finditer(rb"[A-Za-z0-9_./-]{3,160}\.dds\x00", data, re.IGNORECASE):
        value=match.group(0)[:-1].decode("ascii", "replace")
        if value not in embedded_names: embedded_names.append(value)
    while True:
        offset=data.find(b"DDS ", cursor)
        if offset < 0: break
        size=dds_size(data, offset)
        if size <= 0: cursor=offset+4; continue
        height,width=struct.unpack_from("<2I", data, offset+12)
        fourcc=data[offset+84:offset+88].decode("ascii", "replace").strip("\0")
        item={"index":len(textures),"offset":offset,"size":size,"width":width,"height":height,"format":fourcc or "uncompressed"}
        if extract_dir is not None:
            extract_dir.mkdir(parents=True, exist_ok=True)
            out=extract_dir / f"texture_{len(textures):04d}_{offset:08x}.dds"
            out.write_bytes(data[offset:offset+size]); item["file"]=str(out)
        textures.append(item)
        cursor=offset+size
    if not textures:
        textures = inspect_headerless_xtd(data, extract_dir, embedded_names)
    report = {"format":"RDR1 WTD texture inventory", "source":str(path), "sha256":source_sha, "status":"validated DDS headers" if any(not x.get("headerReconstructed") for x in textures) else "reconstructed XTD DDS payloads", "textureCount":len(textures), "embeddedTextureNames":embedded_names, "textures":textures, "unresolved":["WTD dictionary name/hash semantics and shader slot semantics require further validation"]}
    report.update(dictionary_metadata(data))
    return report

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("input",type=Path); p.add_argument("--output",type=Path,required=True); p.add_argument("--extract-dir",type=Path); a=p.parse_args()
    report=inspect(a.input, a.extract_dir); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"textureCount":report.get("textureCount",0),"status":report.get("status")})); return 0
if __name__ == "__main__": raise SystemExit(main())
