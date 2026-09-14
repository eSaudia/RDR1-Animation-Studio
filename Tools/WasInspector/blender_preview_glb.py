"""Export only the active character action and skinned meshes, never helper dots."""
import sys
import bpy

output = sys.argv[sys.argv.index('--') + 1]
rigs = [o for o in bpy.data.objects if o.type == 'ARMATURE']
if len(rigs) != 1:
    raise ValueError('Preview requires exactly one matching character rig')
rig = rigs[0]
meshes = [o for o in bpy.data.objects if o.type == 'MESH' and any(
    m.type == 'ARMATURE' and m.object == rig for m in o.modifiers)]
if not meshes:
    raise ValueError('No skinned character mesh found; refusing a dots-only preview')
bpy.ops.object.select_all(action='DESELECT')
for o in [rig] + meshes:
    o.hide_set(False)
    # The bundled base blend hides the armature at the object level.  Clearing
    # only the view-layer flag still makes Blender omit the rig from glTF.
    o.hide_viewport = False
    o.hide_render = False
    o.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.export_scene.gltf(filepath=output, export_format='GLB', use_selection=True,
    export_animations=True, export_animation_mode='ACTIVE_ACTIONS', export_force_sampling=True,
    export_skins=True, export_materials='EXPORT')
