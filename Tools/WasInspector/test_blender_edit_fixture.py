"""Manual Blender integration fixture: shift one exported translation curve."""
import sys

def main():
    import bpy
    source, output = sys.argv[sys.argv.index('--')+1:]
    bpy.ops.wm.open_mainfile(filepath=source, use_scripts=False)
    action = next(a for a in bpy.data.actions if a.get('rdr1_source_sha256'))
    curves = [f for layer in action.layers for strip in layer.strips for bag in strip.channelbags for f in bag.fcurves]
    curve = next(f for f in curves if f.data_path.endswith('.location') and f.array_index == 0 and len(f.keyframe_points)>2)
    for p in curve.keyframe_points:
        p.co.y += 0.001
        p.handle_left.y += 0.001
        p.handle_right.y += 0.001
    print('EDITED',curve.data_path,curve.array_index)
    bpy.ops.wm.save_as_mainfile(filepath=output)

if __name__=='__main__': main()
