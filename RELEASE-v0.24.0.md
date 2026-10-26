# RDR1 Animation Studio v0.24.0 — validated WVD mesh evidence

This milestone extends the bounded world-resource decoder from dictionary
pointer validation to actual visual mesh payloads. It does not claim a complete
world reconstruction: every field remains evidence-backed and unresolved
fields are listed in the generated JSON.

## What is decoded

- WVD visual-dictionary hashes and drawable pointer pages.
- Drawable headers, bounding boxes, LOD pointers, and LOD distances.
- LOD model pointer arrays and model metadata.
- Geometry headers, primitive type, vertex/index counts, and buffer pointers.
- Vertex declarations (FVF bitmask, component types, byte offsets, and stride).
- Float3/Float4 POSITION channels with RDR-to-tool axis conversion.
- Indexed triangle data and computed mesh bounds when pointer/range checks pass.

## Evidence from the DLC_Find_Blunderbuss candidate cell

The four WVD files in the Armadillo blacksmith candidate cell decode to:

| Resource | Drawables | Models | Geometries | Vertices | Indices |
| --- | ---: | ---: | ---: | ---: | ---: |
| arm_blacksmith01props01x.wvd | 21 | 21 | 22 | 11,451 | 22,059 |
| arm_blacksmith01props01x_med.wvd | 8 | 8 | 10 | 3,035 | 5,403 |
| arm_blacksmith01x.wvd | 1 | 1 | 15 | 14,940 | 25,065 |
| arm_blacksmith01x_med.wvd | 1 | 2 | 9 | 1,702 | 2,955 |
| **Total** | **31** | **32** | **56** | **31,128** | **55,482** |

The generated reports contain source offsets, expected/reference VFTs,
pointer targets, vertex declarations, decoded positions, index arrays, and
per-geometry bounds. The six WBD reports still provide collision bounds,
quantized vertices, and polygon records.

## Still unresolved by design

- Shader groups, material/texture bindings, and drawable-to-shader mapping.
- Sector placement instances and the mission script's world-cell identity.
- Terrain/nav data, streaming-sector transforms, and camera-parent coordinates.
- Non-Float3/Float4 position channels and non-triangle primitive expansion.

Unknown layouts are reported as unresolved instead of being silently guessed.
