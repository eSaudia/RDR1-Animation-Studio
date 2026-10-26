# RDR1 Animation Studio v0.27.0

## Full Armadillo territory mesh coverage

The previous placed scene used only the blacksmith WVD files. This release
expands extraction to every `.wbd` and `.wvd` under `root/armadillo/` in the
selected territory archive, plus `armadillo.wsi`. WSI placement hashes can now
resolve against the complete visual-dictionary set available in that archive.

### Added

- World-cell evidence extraction includes the full Armadillo territory set.
- Hash joining and Blender placement operate across all extracted WVD reports.
- The manifest now describes the candidate as the Armadillo territory rather
  than a single blacksmith asset.

### Boundary

This is broader visual coverage, not a claim of mission ownership. WVD
materials/textures, terrain, room/portal streaming, audio, AI, camera timing,
and the mission VM remain separate decoder work.
