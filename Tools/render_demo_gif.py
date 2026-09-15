import bpy
import os

out = os.environ.get('RDR_GIF_FRAMES', '')
if not out:
    raise RuntimeError('RDR_GIF_FRAMES is required')
os.makedirs(out, exist_ok=True)
scene = bpy.context.scene
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'STUDIO'
scene.display.shading.studio_light = 'paint.sl'
scene.display.shading.color_type = 'MATERIAL'
scene.render.resolution_x = 640
scene.render.resolution_y = 640
scene.render.resolution_percentage = 70
scene.render.image_settings.file_format = 'PNG'
scene.render.film_transparent = False
scene.world.color = (0.025, 0.035, 0.05)
if scene.camera is None:
    import mathutils
    visible = [o for o in scene.objects if o.type in {'MESH', 'ARMATURE'}]
    points = [o.matrix_world @ mathutils.Vector(c) for o in visible for c in o.bound_box] if visible else [mathutils.Vector((0,0,0))]
    center = sum(points, mathutils.Vector()) / len(points)
    radius = max((p-center).length for p in points) or 1.0
    cam_data = bpy.data.cameras.new('DemoCamera')
    cam = bpy.data.objects.new('DemoCamera', cam_data)
    scene.collection.objects.link(cam)
    cam.location = center + mathutils.Vector((radius * 1.45, -radius * 1.8, radius * 0.95))
    direction = center - cam.location
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    cam_data.lens = 52
    scene.camera = cam
scene.frame_start = max(scene.frame_start, 1)
scene.frame_end = min(scene.frame_end, scene.frame_start + 59)
for frame in range(scene.frame_start, scene.frame_end + 1, 2):
    scene.frame_set(frame)
    scene.render.filepath = os.path.join(out, f'frame_{frame:04d}.png')
    bpy.ops.render.render(write_still=True)
