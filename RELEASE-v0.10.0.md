# RDR1 Animation Studio v0.10.0 — Animation Lab

## Character-aware preview

- Added a Character picker to the Library page.
- The bundled John human rig remains available without a game scan.
- A game-folder scan now exposes discovered `.wft` character models by name.
- WFT geometry is decoded on demand, cached under `%LocalAppData%\\RDR1AnimationStudio\\Characters`, merged with the selected animation, and converted to a self-contained GLB for the in-app viewer.
- Invalid, incompatible, or unsupported resources fail with an explanation instead of falling back to dots or a different character.

## Scope

The v0.10 character path is a preview milestone. Blender export remains skeleton-first on the bundled player mapping so the existing multi-action export and experimental WAS round-trip stay stable. RPF archives are never modified automatically.

## Verification

- `dotnet build -c Release --no-restore`
- WFT geometry and glTF merge tests: 3 passed.
- Blender 5.2 conversion smoke test: merged character glTF converted to GLB successfully.
