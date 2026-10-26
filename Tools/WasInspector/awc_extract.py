#!/usr/bin/env python3
"""Extract standard RIFF/WAVE members from a Rockstar ADAT .awc bank."""
from __future__ import annotations
import argparse
import json
import struct
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--manifest", type=Path, default=None)
    args = parser.parse_args()
    data = args.input.read_bytes()
    args.output.mkdir(parents=True, exist_ok=True)
    count = 0
    members = []
    cursor = 0
    while True:
        offset = data.find(b"RIFF", cursor)
        if offset < 0 or offset + 12 > len(data):
            break
        size = struct.unpack_from("<I", data, offset + 4)[0]
        end = offset + 8 + size
        if data[offset + 8:offset + 12] == b"WAVE" and end <= len(data):
            target = args.output / f"{args.input.stem}-{count + 1:02}.wav"
            payload = data[offset:end]
            target.write_bytes(payload)
            fmt_offset = payload.find(b"fmt ")
            fmt = {}
            if fmt_offset >= 0 and fmt_offset + 24 <= len(payload):
                chunk_size = struct.unpack_from("<I", payload, fmt_offset + 4)[0]
                if chunk_size >= 16 and fmt_offset + 24 <= len(payload):
                    tag, channels, sample_rate, avg_bytes, block_align, bits = struct.unpack_from("<HHIIHH", payload, fmt_offset + 8)
                    fmt = {
                        "formatTag": tag,
                        "channels": channels,
                        "sampleRate": sample_rate,
                        "averageBytesPerSecond": avg_bytes,
                        "blockAlign": block_align,
                        "bitsPerSample": bits,
                        "formatChunkBytes": chunk_size,
                    }
            data_offset = payload.find(b"data")
            data_bytes = 0
            if data_offset >= 0 and data_offset + 8 <= len(payload):
                data_bytes = struct.unpack_from("<I", payload, data_offset + 4)[0]
            members.append({
                "file": str(target.resolve()),
                "offset": offset,
                "bytes": len(payload),
                "dataBytes": data_bytes,
                "format": fmt,
            })
            count += 1
        cursor = offset + 4
    if count == 0:
        raise SystemExit("No embedded RIFF/WAVE members found")
    manifest = args.manifest or (args.output / "awc-manifest.json")
    manifest.write_text(json.dumps({
        "format": "rdr1-awc-extraction-v1",
        "source": str(args.input.resolve()),
        "memberCount": count,
        "members": members,
        "notes": [
            "Members are extracted byte-for-byte from the embedded RIFF/WAVE records.",
            "Microsoft ADPCM (formatTag 2) is preserved; Blender/FFmpeg is responsible for playback decode.",
        ],
    }, indent=2) + "\n", encoding="utf-8")
    print(f"extracted={count} output={args.output.resolve()} manifest={manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
