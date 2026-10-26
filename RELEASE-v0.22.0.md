# RDR1 Animation Studio v0.22.0 — world-cell evidence extraction

This milestone adds a reproducible first step toward real mission-world
reconstruction:

- **Extract world-cell evidence…** appears on the Scenes page.
- The selected mission (preferring the `.wsc` resource) and its `.sco` companion,
  matching camera resource, related WAS clips, matching AWC bank, and the
  Armadillo `arm_blacksmith01` WBD/WVD candidate cell are copied byte-for-byte.
- `world-cell.manifest.json` records exact archive/internal paths, sizes, SHA-256
  hashes, roles, and a per-file confidence note.
- The package explicitly marks the candidate cell as unverified. Mission-to-cell
  identity, WBD/WVD geometry/placement, collision/terrain/nav data, and runtime
  camera-parent transforms remain unresolved until their formats are decoded.

Command-line form:

```powershell
RDR1AnimationStudio.exe --extract-world-cell <game-folder> dlc_find_blunderbuss <output-folder>
```

This is intentionally an evidence milestone, not a fabricated “full scene”.
The output is the stable input set required for the next decoder milestone.
