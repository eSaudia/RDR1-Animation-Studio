# WAS round-trip research — v0.7

## Implemented

The template-preserving encoder reads recognized little-endian quantized integer
streams, packs them again using their original bit widths, preserves unused tail
bits, and places the resulting bytes at their original offsets. Constant payloads,
headers, pointers, padding, and unrecognized objects remain unchanged. Compressed
RSC5 files are recompressed with Zstandard. The original 12-byte header is preserved.

Output is only written after the complete decoded resource compares byte-for-byte
equal with the input. New output filenames are required. The report contains hashes,
stream counts, coverage limitations, and an explicit untested-in-game flag.

This is a stream codec plus resource repacker, not a serializer for the entire WAS
object graph. It does not prove we can encode newly created or edited animations.

## Actual sample evidence

| Sample | Records found | Integer streams rebuilt | Integer samples | Decoded equality |
|---|---:|---:|---:|---|
| gent.was | 612 | 56,273 | 3,104,640 | Exact |
| stand_yelling.was | 4 | 575 | 114,915 | Exact |

For gent, 97 static/special targets are preserved without interpretation. The record
`gent_wag_gatling_hnd_enter` does not expose pointers recognized by the current scanner;
its bytes are also preserved. The `bum.was` sample has no records recognized by this
decoder and is rejected rather than reported as a successful animation reconstruction.

The existing scanner discovers padded pointer runs. Their lengths do not consistently
match the record's presumed array count, so that field's semantics remain unresolved.
These counts are discovered coverage, not proof of exhaustive animation decoding.

36 Python tests pass, including independent known bit-packing bytes, widths 1–32,
partial final bytes, invalid bounds/codes, and refusal to overwrite inputs/outputs.
The .NET release build passes without warnings or errors.

## Next format work

1. Confirm record array fields and static/special object layouts.
2. Map shared stream references before allowing individual clip edits.
3. Add controlled changes with fixed sample counts and quantization bounds; verify
   only intended bytes and decoded tracks change.
4. Recover original local bone transforms from Blender without losing quaternion
   signs, segment boundaries, timing, or root motion.
5. Implement relocation/page sizing for streams that need more storage.
6. Test a rebuilt resource in the game using a separately backed-up installation.

An in-game test has not been performed. Identical decoded bytes are strong evidence
for unchanged resource contents, but do not validate archive replacement, compressed
entry sizing, or acceptance by the game loader.
