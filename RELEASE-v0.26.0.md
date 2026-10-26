# RDR1 Animation Studio v0.26.0

## Placed world-cell Blender scene

This milestone turns the v0.25 placement evidence into a visible Blender
scene. It is the first scene assembled from real RDR1 WVD geometry and WSI
instance transforms rather than the earlier placeholder street block.

### Added

- `blender_world_cell.py` reads `placement-join.json` and creates shared meshes
  from decoded WVD positions and indexed triangles.
- Matched instances receive the exact pointer-validated WSI transform, with
  names and hashes stored as Blender custom properties.
- Unmatched WSI instances remain visible as evidence markers instead of being
  silently discarded.
- The Scenes page now has **Build placed world-cell Blender scene…**. Choose a
  v0.25 evidence package and save a `.blend` preview.
- The generated scene includes a camera, presentation floor, lighting, neutral
  evidence materials, and a `.scene.json` provenance report.

### Smoke result

The Armadillo package generated a valid `.blend` with 57 placed instances and
72 mesh objects from 416 WSI instances; 359 unmatched instances are retained
as evidence markers. A Blender frame render completed successfully.

### Still unresolved

Textures/shaders, terrain, room/portal streaming, mission ownership, camera
cutscene semantics, audio synchronization, and AI behavior are not invented.
