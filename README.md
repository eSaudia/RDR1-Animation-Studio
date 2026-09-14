![RDR1 Animation Studio](Assets/release-banner.png)

# RDR1 Animation Studio 0.11 · Animation Lab

Created by **eSaudia — Creator & Project Owner**.

RDR1 Animation Studio is a focused Windows workbench for exploring the original
Red Dead Redemption animation archives and preparing them for Blender. It is a
research and modding tool: it does not include Rockstar game files and it never
writes to your RPF archives automatically.

## See it in action

The application indexes real WAS clip records, keeps their source identity, and
lets you export a single clip, a selection, or an entire animation set.

![Animation Studio library](Assets/RDR1-Animation-Studio-v0.9.0.png)

Exported actions can be opened in Blender on the bundled human skeleton. The
demo below shows the exported animation workflow and playback timing.

![Animation export demo](Assets/RDR1-Animation-Studio-demo.gif)

![Blender character preview](Assets/RDR1-Animation-Studio-v0.5.0-jack-preview.png)

## What it can do

- Scan a selected RDR1 game directory and discover animation sets across RPF files.
- Add individual extracted `.was` files for focused inspection.
- Show internal clip names, frame counts, FPS, source archive, and support status.
- Detect identical decoded motion without hiding clips merely because their names match.
- Export selected clips or all clips from a WAS set to Blender-compatible files.
- Preview supported clips on the bundled John rig or a discovered WFT character mesh.
- Decode WFT character geometry into a cached, skinned glTF preview without writing to RPF archives.
- Preserve source IDs and validation metadata for experimental Blender round trips.
- Provide a dark/light theme and a report when a clip layout is unsupported.
- Create custom Blender authoring scenes, including a Spider leap demo preset.
- Package selected supported clips into a portable, deduplicated animation pack with a validated manifest.

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

The interface has Library, Authoring, Packs, Import, Scan report, and About sections, custom drawn icons,
and a top-right Theme switch. Dark is the default. The old renderer and filename-database
control have been removed. Filename resolution runs automatically internally.

The **Character** picker is populated after a game-folder scan. **John · bundled human rig**
works without a game model; discovered `.wft` assets are decoded on demand and cached in
`%LocalAppData%\\RDR1AnimationStudio\\Characters`. WFT meshes must use the bundled player
skeleton mapping; incompatible or malformed resources are reported in the preview panel.

## Export to Blender

Select rows using Ctrl/Shift or Select all, then **Export selected**. **Export all** uses
the entire collected library regardless of search or visibility filters. Identical
decoded motion is exported once with aliases in the report. Unsupported clips are
reported rather than replaced with another clip or stretched to fit a guessed timeline.

Exports are named Blender Actions on the bundled human skeleton, in files grouped by
source with at most 50 Actions per file. Character-aware WFT geometry is currently a
preview feature; Blender export remains deliberately skeleton-first so exported files are
stable and compatible with the existing round-trip importer. NLA playback is disabled to
prevent interference with the active Action.
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

## Custom authoring and packs

The **Authoring** section creates a Blender scene from the bundled 119-bone human rig.
Choose an action name, frame count, FPS, and either a blank pose or the **Spider leap**
demo preset. Edit the Action in Blender and save the `.blend` file.

The **Packs** section creates a portable folder from selected supported Library clips.
It stores each source WAS once under `sources/`, writes exact record offsets and timing
to `manifest.json`, and provides a validator. Packs are staged for a future ScriptHookRDR
runtime menu; a loose JSON/WAS folder is not loaded by the game, and no game archive is
overwritten automatically.

### Faster repeat scans

The first scan of a game folder still has to read its RPF archives and decode each WAS
set. RDR1 Animation Studio now stores an archive inventory, extracted-source manifest,
and decoded catalog under `%LOCALAPPDATA%\RDR1AnimationStudio\Cache`. Selecting the
same unchanged game folder again reuses those results; changing an RPF automatically
invalidates the affected cache.

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
