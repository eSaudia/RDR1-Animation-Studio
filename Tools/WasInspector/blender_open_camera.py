"""Prepare the opened export for inspection in Blender."""
import bpy
from bpy.app.handlers import persistent


def prepare_viewport():
    # Blender can run startup scripts before visible 3D areas exist.
    found_view = False
    camera_ready = False
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            found_view = True
            space = area.spaces.active
            try:
                space.region_3d.view_perspective = 'CAMERA'
                camera_ready = camera_ready or space.region_3d.view_perspective == 'CAMERA'
            except Exception:
                pass
            try:
                space.shading.type = 'MATERIAL'
            except Exception:
                pass
            try:
                space.overlay.show_overlays = False
            except Exception:
                pass
            for region in area.regions:
                if region.type == 'WINDOW':
                    try:
                        with bpy.context.temp_override(window=window, area=area, region=region):
                            bpy.ops.view3d.view_camera()
                    except Exception:
                        pass
                    break
    try:
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        bpy.context.view_layer.objects.active = None
    except Exception:
        pass
    try:
        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
    except Exception:
        pass
    # Keep retrying briefly if Blender has not finished creating its visible
    # workspace yet.  Returning a delay keeps the timer alive.
    return None if (found_view and camera_ready) else 0.5


prepare_viewport()
try:
    bpy.app.timers.register(prepare_viewport, first_interval=0.75)
except Exception:
    pass

@persistent
def after_file_load(_dummy):
    try:
        bpy.app.timers.register(prepare_viewport, first_interval=0.8)
    except Exception:
        pass

if after_file_load not in bpy.app.handlers.load_post:
    bpy.app.handlers.load_post.append(after_file_load)
