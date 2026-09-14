"""Import a glTF interchange file and save it as a self-contained preview GLB."""
import sys
import bpy


def main() -> int:
    arguments = sys.argv[sys.argv.index("--") + 1:]
    if len(arguments) != 2:
        raise ValueError("Usage: blender --python blender_gltf_to_glb.py -- input.gltf output.glb")
    source, output = arguments
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=source)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=output,
        export_format="GLB",
        use_selection=True,
        export_animations=True,
        export_animation_mode="ACTIVE_ACTIONS",
        export_force_sampling=True,
        export_skins=True,
        export_materials="EXPORT",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
