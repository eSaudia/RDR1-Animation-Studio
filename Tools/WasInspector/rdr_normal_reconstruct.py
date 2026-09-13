#!/usr/bin/env python3
"""Convert RDR1 ATI2 PNG output into standard RGB tangent normals."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image


def reconstruct_pixel(pixel: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    # RAGE's DXT5nm/ATI2 convention stores X in alpha and Y in green.
    # glTF uses an OpenGL tangent basis, so flip the DirectX green/Y channel.
    _, green, _, alpha = pixel
    x = alpha / 127.5 - 1.0
    y = -(green / 127.5 - 1.0)
    length_sq = x * x + y * y
    if length_sq > 1.0:
        scale = 1.0 / math.sqrt(length_sq)
        x, y, length_sq = x * scale, y * scale, 1.0
    z = math.sqrt(max(0.0, 1.0 - length_sq))
    return (round((x + 1.0) * 127.5), round((y + 1.0) * 127.5),
            round((z + 1.0) * 127.5), 255)


def reconstruct(source: Path, output: Path) -> None:
    image = Image.open(source).convert("RGBA")
    image.putdata([reconstruct_pixel(pixel) for pixel in image.getdata()])
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    count = 0
    for source in args.sources:
        paths = source.rglob("*_n1.png") if source.is_dir() else [source]
        for path in paths:
            destination = args.output / path.name
            reconstruct(path, destination)
            print(destination)
            count += 1
    print(f"Reconstructed {count} normal map(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
