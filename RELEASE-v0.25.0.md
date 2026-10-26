# RDR1 Animation Studio v0.25.0

## Placement reconstruction milestone

This release adds the first pointer-validated world placement layer for the
single-mission Armadillo evidence package. It does not claim that mission
ownership, materials, or room/portal streaming have been decoded.

### Added

- `world_resource_inspector.py` accepts `.wsi` sector resources.
- The Armadillo `armadillo.wsi` root is validated as `Rsc6SectorInfo`.
- 48 scoped sectors and 416 drawable-instance blocks are decoded from the
  real game resource.
- Each valid instance includes its name pointer, 32-bit drawable hash,
  bounding box, flags/room bytes, and RDR-to-tool-axis 4×4 placement matrix.
- `placement_join.py` joins instance hashes to decoded WVD drawable candidates
  without guessing names or materials.
- The desktop world-cell export includes the WSI input, WSI semantic report,
  and `placement-join.json`.

### Observed evidence

The Armadillo sector resource contains hashes that match the companion visual
dictionaries, including `0x1A11F678` (`arm_blacksmith01x`) and
`0x026B5913` (`arm_blacksmith01Props01x`). These are exact hash joins, not
mission-script ownership proofs.

### Still unresolved

- Mission VM world-cell references and runtime branches.
- Secondary drawable-instance records, room/portal membership, and streamed
  cell ownership.
- WVD shader/material/texture bindings, terrain, navigation, and audio sync.
- Camera parent/coordinate semantics and a fully game-faithful scene renderer.
