#!/usr/bin/env python3
"""Render a RDR1 Animation Studio scene to a movie file."""

from __future__ import annotations

import argparse
from pathlib import Path

import bpy


def options() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--end", type=int, default=None, help="Optional last frame for a short test render")
    marker = __import__("sys").argv.index("--") + 1 if "--" in __import__("sys").argv else len(__import__("sys").argv)
    return parser.parse_args(__import__("sys").argv[marker:])


def main() -> int:
    args = options()
    scene = bpy.context.scene
    if args.end is not None:
        scene.frame_end = max(scene.frame_start, min(args.end, scene.frame_end))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.height
    scene.render.resolution_percentage = 100
    scene.render.fps = scene.render.fps or 30
    # Blender 5.x uses media_type to expose the movie formats.  Select VIDEO
    # before choosing FFMPEG or the enum is limited to still-image formats.
    scene.render.image_settings.media_type = "VIDEO"
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
    scene.render.filepath = str(args.output.resolve())
    scene.frame_set(scene.frame_start)
    bpy.ops.render.render(animation=True)
    print(f"Rendered movie: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
