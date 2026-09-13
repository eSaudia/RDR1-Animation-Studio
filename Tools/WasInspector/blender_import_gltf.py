#!/usr/bin/env python3
"""Import an animated glTF into Blender and save it as a blend file."""

import sys
from pathlib import Path

import bpy


def arguments():
    marker = sys.argv.index("--") + 1 if "--" in sys.argv else len(sys.argv)
    values = sys.argv[marker:]
    if len(values) != 2:
        raise RuntimeError("Expected input.gltf and output.blend.")
    return Path(values[0]).resolve(), Path(values[1]).resolve()


source, output = arguments()
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
for datablocks in (bpy.data.actions, bpy.data.armatures, bpy.data.meshes, bpy.data.materials):
    for block in list(datablocks):
        if block.users == 0:
            datablocks.remove(block)
bpy.ops.import_scene.gltf(filepath=str(source))
scene = bpy.context.scene
scene.render.fps = 30
if bpy.data.actions:
    action = bpy.data.actions[0]
    start, end = action.frame_range
    scene.frame_start = max(0, int(start))
    scene.frame_end = max(scene.frame_start + 1, int(end))
    scene.frame_set(scene.frame_start)
output.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(output))
