"""Frame an exported animation scene in a close, front-facing camera view."""
import argparse, bpy, sys
from mathutils import Vector

def main():
    p=argparse.ArgumentParser(); p.add_argument("--input",required=True); p.add_argument("--output",required=True); argv=sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else sys.argv[1:]; a=p.parse_args(argv)
    bpy.ops.wm.open_mainfile(filepath=a.input); scene=bpy.context.scene
    meshes=[o for o in scene.objects if o.type=="MESH" and not o.hide_render]
    points=[o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    if points:
        lo=Vector((min(v.x for v in points),min(v.y for v in points),min(v.z for v in points))); hi=Vector((max(v.x for v in points),max(v.y for v in points),max(v.z for v in points)))
        center=(lo+hi)*0.5; radius=max((hi-lo).length*0.5,1.0)
    else: center=Vector((0,0,1)); radius=2.0
    for o in list(scene.objects):
        if o.type=="CAMERA": bpy.data.objects.remove(o,do_unlink=True)
    bpy.ops.object.camera_add(location=center+Vector((0,-radius*3.0,radius*0.25)))
    cam=bpy.context.object; cam.name="RDR1 front camera"; cam.data.lens=58; cam.data.clip_start=0.01; cam.data.clip_end=max(100,radius*20)
    cam.rotation_euler=(center-cam.location).to_track_quat("-Z","Y").to_euler(); scene.camera=cam
    scene.render.resolution_x=1280; scene.render.resolution_y=720; scene.render.resolution_percentage=100
    scene.frame_set(scene.frame_start)
    bpy.ops.wm.save_as_mainfile(filepath=a.output); print("Focused scene saved:",a.output)
if __name__=="__main__": main()
