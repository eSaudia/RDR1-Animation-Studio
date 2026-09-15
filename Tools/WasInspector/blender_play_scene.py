#!/usr/bin/env python3
"""Open a RDR1 Animation Studio .blend in camera view and start playback."""

from __future__ import annotations

import argparse
import bpy


def options() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    marker = __import__("sys").argv.index("--") + 1 if "--" in __import__("sys").argv else len(__import__("sys").argv)
    parser.add_argument("--start", type=int, default=None)
    parser.add_argument("--no-play", action="store_true")
    return parser.parse_args(__import__("sys").argv[marker:])


def start_playback(args: argparse.Namespace) -> None:
    scene = bpy.context.scene
    if args.start is not None:
        scene.frame_set(max(scene.frame_start, min(args.start, scene.frame_end)))
    else:
        scene.frame_set(scene.frame_start)
    # Put the first 3D viewport into camera view so the user sees the shot,
    # rather than having to find the camera and switch view manually.
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                try:
                    area.spaces.active.region_3d.view_perspective = "CAMERA"
                except Exception:
                    pass
    if not args.no_play:
        try:
            for window in bpy.context.window_manager.windows:
                area = next((candidate for candidate in window.screen.areas if candidate.type == "VIEW_3D"), None)
                if area is not None:
                    with bpy.context.temp_override(window=window, area=area):
                        bpy.ops.screen.animation_play()
                    break
        except RuntimeError:
            # The scene is still usable if Blender has not created a playback
            # context yet; the user can press Spacebar once the window appears.
            pass


def main() -> int:
    args = options()
    bpy.app.timers.register(lambda: (start_playback(args), None)[1], first_interval=1.0)
    return 0


if __name__ == "__main__":
    # Do not raise SystemExit here: Blender treats it as a request to close
    # the interactive window immediately after the script is loaded.
    main()
