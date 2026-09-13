# RDR1 Animation Studio 0.8

Created by **eSaudia — Creator & Project Owner**.

## Library

Choose **Game folder** to collect animation sets from all RPF archives beneath the
selected installation. The initial scan decodes motion to check support and identify
identical content, so it can take several minutes. Progress and Cancel are available.
**Add WAS** also loads extracted animation sets.

The library shows actual internal animation names, source sets, frame counts, timing,
and support status. It does not invent friendly names for unresolved resources.
Each clip is identified by its source and record offset. Identical names are not
treated as evidence of identical motion. **Hide identical motion** uses decoded-track
fingerprints; unrecognized layouts remain visible with an explanation in Scan report.

The interface has Library, Import, Scan report, and About sections, custom drawn icons,
and a top-right Theme switch. Dark is the default. The old renderer and filename-database
control have been removed. Filename resolution runs automatically internally.

## Export to Blender

Select rows using Ctrl/Shift or Select all, then **Export selected**. **Export all** uses
the entire collected library regardless of search or visibility filters. Identical
decoded motion is exported once with aliases in the report. Unsupported clips are
reported rather than replaced with another clip or stretched to fit a guessed timeline.

Exports are named Blender Actions on the bundled human skeleton, in files grouped by
source with at most 50 Actions per file. A matching character mesh is not included in
this workflow. NLA playback is disabled to prevent interference with the active Action.
The output folder includes `export-report.json`, source identities, and glTF files.
Animations incompatible with the supplied human skeleton are reported as unsupported.

## Experimental Blender import

1. Export a supported clip with version 0.8 and edit its Action in Blender.
2. Keep its Action name, skeleton, original 30 FPS timeline, and frame count.
3. Select the original clip in Library, open Import, and choose the edited `.blend`.
4. Save to a new `.was` filename. Inspect the adjacent validation report.

Source hashes and record IDs stored on the Action must match. The importer currently
updates supported tracks in their existing storage layout. It can reject changes that
need larger storage, constant-to-animated changes, shared tracks, unknown layouts,
unsupported bone transforms, scale changes, or excessive quantization error.
Root-motion channels take precedence over ordinary root channels, matching export;
ordinary root channels are preserved. This is not general retargeting or FBX import.

The result is decoded and compared with imported samples. An unchanged Blender round
trip and a small root-motion edit were verified using a real WAS sample. In-game loader
acceptance has not been tested. RPF archives are never written automatically.

## Requirements / build

Windows x64, .NET 10 Desktop Runtime (or SDK), Python 3.12 x64, and Blender. A Python 3.12
zstandard dependency is included. Blender 5.2 was used for integration tests.

```powershell
dotnet build -c Release
dotnet publish -c Release -r win-x64 --self-contained false
```

46 Python tests cover codecs, duplicate identity, timing rejection, import writes,
shared-track rejection, and existing decoder behavior. See WAS-ROUNDTRIP.md for the
earlier encoder research; that document describes version 0.7, not the current UI.
