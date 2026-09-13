# RDR1 Animation Studio v0.9.0

RDR1 Animation Studio is an independent Windows tool for exploring Red Dead Redemption animation resources.

## Highlights

- Scan a complete RDR1 game directory and build one searchable animation library.
- Preserve exact in-game resource names and source locations.
- Detect identical motion by decoded track fingerprints instead of names alone.
- Preview supported clips on a real skinned human character before export.
- Export selected clips or the complete library to Blender actions.
- Import edited Blender actions into a new WAS file with validation; the original archive is never overwritten.
- Dark/light theme, RDR1-branded icon, and creator credit: eSaudia.

## Requirements

- Windows 10/11 x64
- .NET 10 Desktop Runtime (or SDK)
- Blender 3.6+ for preview/export/import
- Python 3.12 for resource decoding
- Microsoft Edge WebView2 Runtime for the in-app 3D preview

## Safety

The importer writes a new `.was` and a validation report. It does not patch game archives automatically. Back up game files before testing mods.

## Downloads

Use the accompanying `RDR1-Animation-Studio-v0.9.0-win-x64.zip` release archive. Source is provided in the matching `-source.zip` archive.

Created by eSaudia.
