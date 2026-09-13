"""Game-wide catalog with source identities, conservative validation and Blender export."""
import argparse
import collections
import hashlib
import json
import pathlib
import struct
import sys

import was_animation_export as animation
import was_batch_export as batch
import was_gltf_export as gltf
import was_skeleton_bind as skeleton


def validate_motion(document):
    entries = document['entries']
    if len(entries) != 1 or entries[0]['kind'] != 'compressed_sequence':
        raise ValueError('Unresolved sequence layout; exact export unavailable')
    entry = entries[0]
    if entry['frame_count'] != document['animation']['frame_count']:
        raise ValueError('Sequence timing differs from clip timing; export blocked')
    keys = set()
    tracks = []
    for channel in entry['channels']:
        track = channel['assembled_track']
        if track.get('status') != 'decoded' or track.get('samples_truncated', 0):
            raise ValueError('Contains an undecoded or incomplete track')
        key = (channel['bone_id'], channel['track_kind'])
        if key in keys:
            raise ValueError('Ambiguous repeated bone track')
        keys.add(key)
        samples = track['samples']
        if len(samples) != entry['frame_count']:
            raise ValueError('Incomplete frame samples')
        tracks.append((key, samples))
    if not tracks:
        raise ValueError('No motion tracks')
    digest = hashlib.sha256()
    digest.update(struct.pack('<Id', document['animation']['frame_count'], document['animation']['frames_per_second']))
    for (bone, kind), samples in sorted(tracks):
        digest.update(struct.pack('<II', bone, kind))
        for values in samples:
            digest.update(struct.pack('<' + 'd' * len(values), *values))
    return digest.hexdigest()


def build_catalog(manifest, output):
    rows, errors = [], []
    for index, source in enumerate(manifest):
        path = pathlib.Path(source['File'])
        try:
            data, records = batch.catalog(path)
            if not records:
                errors.append({'source': source['Label'], 'error': 'No recognized animation records'})
            for record in records:
                row = dict(Source=str(path.resolve()), Set=source['Label'], Offset=record['offset'],
                           Name=record['name'], Frames=record['frame_count'], Fps=record['inferred_fps'],
                           Duration=record['duration_seconds'], Fingerprint='', Status='Supported', DuplicateOf='')
                try:
                    doc = animation.export_animation(path, record['name'], 0, data, records, record['offset'])
                    row['Fingerprint'] = validate_motion(doc)
                except (ValueError, RuntimeError, KeyError, struct.error) as exc:
                    row['Status'] = str(exc)
                rows.append(row)
        except (OSError, ValueError, RuntimeError, struct.error) as exc:
            errors.append({'source': source['Label'], 'error': str(exc)})
        print(f'Indexed {index+1}/{len(manifest)} sets; {len(rows)} clips', flush=True)
    seen = {}
    for row in rows:
        fp = row['Fingerprint']
        if fp:
            if fp in seen:
                row['DuplicateOf'] = seen[fp]
            else:
                seen[fp] = row['Set'] + ' / ' + row['Name'] + ' @ ' + str(row['Offset'])
    output.write_text(json.dumps({'Clips': rows, 'Errors': errors}, indent=2), encoding='utf-8')


def export_library(rows, output, blender, skeleton_path, base_blend=None):
    model, bones = skeleton.load_skeleton(skeleton_path)
    output.mkdir(parents=True, exist_ok=True)
    report = {'exported': [], 'failed': [], 'identical_motion_aliases': []}
    seen_motion = {}
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row['Source']].append(row)
    for source, selected in groups.items():
        try:
            data, records = batch.catalog(pathlib.Path(source))
            # Bounded Blender files keep large whole-game exports usable.
            for start in range(0, len(selected), 50):
                bound, identities = [], []
                for row in selected[start:start+50]:
                    try:
                        doc = animation.export_animation(pathlib.Path(source), row['Name'], 0, data, records, row['Offset'])
                        fingerprint = validate_motion(doc)
                        if row.get('Fingerprint') and fingerprint != row['Fingerprint']:
                            raise ValueError('Source motion changed since indexing. Rescan first.')
                        if fingerprint in seen_motion:
                            report['identical_motion_aliases'].append({'clip':row,'same_as':seen_motion[fingerprint]})
                            continue
                        item = skeleton.bind(doc, model, bones)
                        if not item['binding_coverage']['complete']:
                            raise ValueError('Selected skeleton cannot bind every track')
                        item['animation']['name'] = row['Name'][:40] + '__' + format(row['Offset'], 'x')
                        bound.append(item)
                        identities.append(row)
                        seen_motion[fingerprint] = row
                    except (ValueError, RuntimeError, KeyError) as exc:
                        report['failed'].append({'clip': row, 'error': str(exc)})
                if not bound:
                    continue
                tag = hashlib.sha256(source.encode()).hexdigest()[:10] + f'_{start//50+1:03}'
                target = output / (pathlib.Path(selected[0]['Set']).stem.replace('/', '_').replace('\\', '_').replace(':', '_')[-80:] + '_' + tag)
                interchange = target.with_suffix('.gltf')
                document = gltf.export_gltf_batch(bound)
                source_hash = hashlib.sha256(pathlib.Path(source).read_bytes()).hexdigest()
                document['extras']['studioRecords'] = {item['animation']['name']: {'source_sha256':source_hash,'record_offset':row['Offset']} for item,row in zip(bound,identities)}
                interchange.write_text(json.dumps(document, separators=(',', ':')), encoding='utf-8')
                batch.create_blend(blender, interchange, target.with_suffix('.blend'), base_blend)
                report['exported'].append({'file': str(target.with_suffix('.blend')), 'clips': identities})
        except (ValueError, RuntimeError, OSError) as exc:
            report['failed'].append({'source': source, 'error': str(exc)})
        print(f'Exported {len(report["exported"])} Blender files', flush=True)
    (output / 'export-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if not report['exported']:
        raise ValueError('No clips exported. Read export-report.json for details.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['index', 'export'])
    parser.add_argument('manifest', type=pathlib.Path)
    parser.add_argument('output', type=pathlib.Path)
    parser.add_argument('--blender', type=pathlib.Path)
    parser.add_argument('--skeleton', type=pathlib.Path)
    parser.add_argument('--base-blend', type=pathlib.Path)
    args = parser.parse_args()
    rows = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    if args.mode == 'index':
        build_catalog(rows, args.output)
    else:
        export_library(rows, args.output, args.blender, args.skeleton, args.base_blend)


if __name__ == '__main__':
    main()
