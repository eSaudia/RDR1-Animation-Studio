#!/usr/bin/env python3
"""Unpack an RDR1 PC RSC05/RSC85 resource into its system/graphics byte stream."""

from __future__ import annotations

import argparse
import io
import struct
import zlib
from pathlib import Path

import zstandard

from rsc85_crypto import decrypt_payload


RSC05 = b"RSC\x05"
RSC06 = b"RSC\x06"
RSC85 = b"RSC\x85"
RSC86 = b"RSC\x86"


def unpack(data: bytes) -> bytes:
    if len(data) < 12 or data[:4] not in {RSC05, RSC06, RSC85, RSC86}:
        raise ValueError("Input is not an RDR1 RSC resource.")
    header_size = 16 if data[:4] in {RSC85, RSC86} else 12
    payload = data[header_size:]
    if data[:4] in {RSC85, RSC86}:
        version = struct.unpack_from("<I", data, 4)[0]
        if version != 2:
            raise ValueError(f"Unsupported RSC85/RSC86 version {version}; only v2 AES resources are validated.")
        payload = decrypt_payload(payload)
    if payload.startswith(b"\x28\xB5\x2F\xFD"):
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(payload)) as reader:
            return reader.read()
    try:
        return zlib.decompress(payload)
    except zlib.error:
        return zlib.decompress(payload, -zlib.MAX_WBITS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resource", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    result = unpack(args.resource.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(result)
    version = struct.unpack_from("<I", args.resource.read_bytes(), 4)[0]
    print(f"Unpacked RSC version {version}: {len(result)} bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
