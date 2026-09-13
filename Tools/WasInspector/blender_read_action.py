"""Read one exported Action through Blender's glTF exporter into local bone samples."""
import json
import hashlib
import pathlib
import struct
import sys
import bpy

blend, request, output, skeleton = sys.argv[sys.argv.index('--')+1:]
row = json.loads(pathlib.Path(request).read_text(encoding='utf-8-sig'))
if abs(row['Fps']-30)>0.001:
    raise RuntimeError('This importer currently requires an original 30 FPS clip')
bpy.ops.wm.open_mainfile(filepath=blend, use_scripts=False)
name = row['Name'][:40] + '__' + format(row['Offset'], 'x')
action = bpy.data.actions.get(name)
if action is None:
    raise RuntimeError('The exact exported Action is missing. Keep its original name: ' + name)
if action.get('rdr1_source_sha256') != hashlib.sha256(pathlib.Path(row['Source']).read_bytes()).hexdigest() or action.get('rdr1_record_offset') != row['Offset']:
    raise RuntimeError('This Action does not belong to the selected source WAS and record. Export it with version 0.8 first.')
rigs = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE']
if len(rigs) != 1:
    raise RuntimeError('Import requires exactly one exported armature.')
rig = rigs[0]
rig.animation_data_create()
rig.animation_data.use_nla = False
rig.animation_data.action = action
if action.slots:
    rig.animation_data.action_slot = action.slots[0]
scene = bpy.context.scene
scene.render.fps = 30
scene.frame_start = 0
scene.frame_end = row['Frames'] - 1
if abs(action.frame_range[0]) > 0.1 or abs(action.frame_range[1] - scene.frame_end) > 0.1:
    raise RuntimeError('Changing the clip duration is not supported yet.')
path = pathlib.Path(output).with_suffix('.glb')
bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', export_animation_mode='ACTIVE_ACTIONS', export_animations=True, export_force_sampling=True, export_frame_range=True)
raw = path.read_bytes()
length = struct.unpack_from('<I', raw, 12)[0]
doc = json.loads(raw[20:20+length])
bin_start = 20 + length + 8
buffer = raw[bin_start:]
def accessor(index):
    a = doc['accessors'][index]
    if a['componentType'] != 5126 or 'sparse' in a:
        raise RuntimeError('Unsupported animation accessor')
    v = doc['bufferViews'][a['bufferView']]
    width = {'SCALAR':1, 'VEC3':3, 'VEC4':4}[a['type']]
    start = v.get('byteOffset',0) + a.get('byteOffset',0)
    stride = v.get('byteStride',width*4)
    return [list(struct.unpack_from('<'+'f'*width,buffer,start+i*stride)) for i in range(a['count'])]
if len(doc.get('animations',[])) != 1:
    raise RuntimeError('Expected exactly one active exported animation')
anim = doc['animations'][0]
tracks = []
for channel in anim['channels']:
    target = channel['target']
    node = doc['nodes'][target['node']]
    if target['path'] == 'scale':
        sampler = anim['samplers'][channel['sampler']]
        if any(abs(x-1)>1e-5 for v in accessor(sampler['output']) for x in v):
            raise RuntimeError('Bone scale edits are not supported by WAS import')
        continue
    if target['path'] not in ('translation','rotation'):
        continue
    sampler = anim['samplers'][channel['sampler']]
    times = [v[0] for v in accessor(sampler['input'])]
    values = accessor(sampler['output'])
    if len(times) == 1 or all(v == values[0] for v in values):
        values = [values[0]] * row['Frames']
    elif len(times) != row['Frames'] or any(abs(t-i/30) > 1e-4 for i,t in enumerate(times)):
        raise RuntimeError('Exported sampling does not match the original 30 FPS timeline')
    tracks.append({'name': node.get('name'), 'path': target['path'], 'samples': values})
pathlib.Path(output).write_text(json.dumps({'Action':name,'Frames':row['Frames'],'Tracks':tracks}),encoding='utf-8')
print('Read',len(tracks),'bone tracks')
