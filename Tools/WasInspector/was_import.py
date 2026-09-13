"""Experimental fixed-layout Blender sample import into an existing RSC5 WAS."""
import hashlib
import json
import math
import pathlib
import struct
import sys
import zstandard
import was_inspector as wi
import was_animation_export as ae
import was_skeleton_bind as sb
from was_roundtrip import pack_codes
from studio_library import validate_motion


def ranges(data, offset):
    p = wi.parse_channel_payload(data, offset)
    if p is None:
        raise ValueError('Unknown channel storage prevents safe import')
    result = [(offset, offset + wi.CHANNEL_PAYLOAD_CLASSES[int(p['signature'],16)][1])]
    if p['class'] in ('constant_quaternion','constant_vector3'):
        result.append((p['data_offset'],p['data_offset']+16))
    elif p['class'] == 'sampled_stream_06':
        n = (p['field_0c']*p['field_10']+7)//8
        result.append((p['data_offset'],p['data_offset']+n))
    elif p['class'] == 'component_stream_0e':
        for axis in p['components']:
            h = axis['data_offset']
            if h is not None:
                _, pointer, width, count = struct.unpack_from('<fIII',data,h)
                s = pointer & wi.POINTER_MASK
                result.extend([(h,h+16),(s,s+(width*count+7)//8)])
    return result


def import_samples(source, offset, edited, bones, output):
    if output.exists() or output.resolve() == source.resolve():
        raise ValueError('Choose a new WAS output filename')
    raw, typ, flags, compression, data = wi.load_rsc5(source)
    if typ != 6 or len(data) != flags['total_size']:
        raise ValueError('Unsupported resource container')
    records = wi.scan_animation_source_records(data)
    doc = ae.export_animation(source,'',0,data,records,offset)
    validate_motion(doc)
    if edited['Frames'] != doc['animation']['frame_count']:
        raise ValueError('Clip length changes are not supported')
    names = {b['bone_id']:b['name'] for b in bones}
    tracks = {(t['name'],t['path']):t['samples'] for t in edited['Tracks']}
    if len(tracks) != len(edited['Tracks']):
        raise ValueError('Duplicate imported bone channels')
    result = bytearray(data)
    changed = []
    def write(pos, value):
        if pos < 0 or pos+len(value)>len(data): raise ValueError('Storage outside source')
        if data[pos:pos+len(value)] == value: return
        for a,b in changed:
            left,right = max(a,pos),min(b,pos+len(value))
            if left<right and result[left:right] != value[left-pos:right-pos]:
                raise ValueError('Conflicting shared channel writes')
        result[pos:pos+len(value)] = value
        changed.append((pos,pos+len(value)))
    def scalar(values, value_pos, header=None, scale_pos=None, width=None, count=None, stream=None):
        if not all(math.isfinite(v) for v in values): raise ValueError('Non-finite sample')
        if header is not None:
            _,pointer,width,count = struct.unpack_from('<fIII',data,header)
            stream,scale_pos = pointer & wi.POINTER_MASK,header
        if stream is None:
            if max(values)-min(values)>1e-5: raise ValueError('A constant track cannot become animated in fixed-layout import')
            write(value_pos,struct.pack('<f',values[0])); return
        if count != len(values) or not 1<=width<=32: raise ValueError('Stream dimensions changed')
        base = struct.unpack_from('<f',data,value_pos)[0]
        scale = struct.unpack_from('<f',data,scale_pos)[0]
        maximum=(1<<width)-1
        if scale<=0 or min(values)<base or max(values)>base+scale*maximum:
            base=struct.unpack('<f',struct.pack('<f',min(values)))[0]
            scale=struct.unpack('<f',struct.pack('<f',(max(values)-base)/maximum))[0] if max(values)>base else 0.0
        codes=[min(maximum,max(0,round((v-base)/scale))) if scale else 0 for v in values]
        n=(width*count+7)//8
        write(value_pos,struct.pack('<f',base)); write(scale_pos,struct.pack('<f',scale)); write(stream,pack_codes(codes,width,data[stream:stream+n]))
    targets = {}
    selected_channels = {}
    for channel in doc['entries'][0]['channels']:
        key = (channel['bone_id'], 'translation' if 'vector' in channel['track_kind_name'] else 'rotation')
        if key not in selected_channels or channel['track_kind_name'].startswith('root_'):
            selected_channels[key] = channel
    for channel in selected_channels.values():
        bone = channel['bone_id']; path='translation' if 'vector' in channel['track_kind_name'] else 'rotation'
        key=(names.get(bone),path)
        if key in targets: raise ValueError('Root and ordinary tracks overlap; import unsupported')
        if key not in tracks: raise ValueError('Missing imported bone track: '+str(key))
        values=tracks[key]
        if len(values)!=edited['Frames']: raise ValueError('Incomplete samples')
        width=3 if path=='translation' else 4
        if any(len(v)!=width or not all(math.isfinite(x) for x in v) for v in values): raise ValueError('Invalid samples')
        original=channel['assembled_track']['samples']
        if path=='rotation':
            values=[[x/(sum(a*a for a in q)**0.5 or 1) for x in q] for q in values]
            values=[[-x for x in q] if sum(a*b for a,b in zip(q,o))<0 else q for q,o in zip(values,original)]
        targets[key]=values
        if max(abs(x-y) for q,o in zip(values,original) for x,y in zip(q,o))<2e-5: continue
        payloads=channel['payloads']
        if len(payloads)==1:
            p=wi.parse_channel_payload(data,int(payloads[0]['offset_hex'],16)); pos=p['offset']
            if p['class'] in ('constant_vector3','constant_quaternion'):
                if any(max(abs(a-b) for a,b in zip(v,values[0]))>1e-5 for v in values): raise ValueError('Constant transform cannot become animated')
                write(p['data_offset'],struct.pack('<'+'f'*width,*values[0]))
            elif p['class']=='component_stream_0e':
                if any(v[3]<-1e-5 for v in values): raise ValueError('Packed quaternion requires nonnegative W')
                for axis,c in enumerate(p['components']): scalar([v[axis] for v in values],pos+8+axis*8,c['data_offset'])
            else: raise ValueError('Unsupported channel encoding')
        elif len(payloads)==width:
            for axis,payload in enumerate(payloads):
                p=wi.parse_channel_payload(data,int(payload['offset_hex'],16)); pos=p['offset']
                if p['class']=='constant_float': scalar([v[axis] for v in values],pos+8)
                elif p['class']=='sampled_stream_06': scalar([v[axis] for v in values],pos+0x18,scale_pos=pos+0x14,width=p['field_0c'],count=p['field_10'],stream=p['data_offset'])
                else: raise ValueError('Unsupported scalar channel')
        else: raise ValueError('Unsupported channel layout')
    by_name = {b['name']: b for b in bones}
    for (name, path), values in tracks.items():
        if (name, path) in targets: continue
        bone = by_name.get(name)
        if bone is None: raise ValueError('Unrecognized imported transform: ' + str(name))
        rest = bone['position'] if path == 'translation' else bone['rotation_xyzw']
        for value in values:
            error = max(abs(a-b) for a,b in zip(value,rest))
            if path == 'rotation': error = min(error,max(abs(a+b) for a,b in zip(value,rest)))
            if error > 2e-5: raise ValueError('Edited bone has no writable source track: ' + name)
    if changed:
        for record in records:
            if record['offset']==offset: continue
            pointers=wi.scan_padded_pointer_table(data,record['track_data_offset'])
            if not pointers: raise ValueError('Unresolved other clip prevents safe shared-track validation')
            for pointer in pointers:
                seq=wi.parse_compressed_sequence(data,pointer)
                if seq is None: raise ValueError('Unknown shared object prevents safe import')
                for channel in seq['descriptors']:
                    for p in channel['component_offsets']:
                        if any(max(a,c)<min(b,d) for a,b in changed for c,d in ranges(data,p)):
                            raise ValueError('Edited track is shared by another animation; relocation required')
    verified=ae.export_animation(source,'',0,bytes(result),records,offset)
    validate_motion(verified)
    max_error=0.0
    selected_offsets = {c['descriptor_offset_hex'] for c in selected_channels.values()}
    for channel in verified['entries'][0]['channels']:
        if channel['descriptor_offset_hex'] not in selected_offsets: continue
        path='translation' if 'vector' in channel['track_kind_name'] else 'rotation'
        wanted=targets[(names[channel['bone_id']],path)]
        actual=channel['assembled_track']['samples']
        max_error=max(max_error,max(abs(x-y) for a,b in zip(wanted,actual) for x,y in zip(a,b)))
    if max_error>0.002: raise ValueError(f'Quantization error too high: {max_error}')
    payload=zstandard.ZstdCompressor(level=9).compress(result) if compression=='zstandard' else bytes(result)
    candidate=raw[:12]+payload
    if compression=='zstandard' and wi.decompress_zstd(payload)!=bytes(result): raise ValueError('Recompression check failed')
    report={'source_sha256':hashlib.sha256(raw).hexdigest(),'output_sha256':hashlib.sha256(candidate).hexdigest(),'changed_ranges':changed,'maximum_component_error':max_error,'in_game_tested':False,'mode':'fixed-layout experimental import'}
    report_path=output.with_suffix('.was.import.json')
    if report_path.exists(): raise ValueError('Report already exists')
    with output.open('xb') as f: f.write(candidate)
    with report_path.open('x') as f: json.dump(report,f,indent=2)
    return report


if __name__=='__main__':
    request,samples,output=map(pathlib.Path,sys.argv[1:])
    row=json.loads(request.read_text(encoding='utf-8-sig'))
    _,bones=sb.load_skeleton(pathlib.Path(__file__).resolve().parents[2]/'Assets/player_default.skeleton.csv')
    print(json.dumps(import_samples(pathlib.Path(row['Source']),row['Offset'],json.loads(samples.read_text()),bones,output),indent=2))
