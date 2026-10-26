# RDR1 Animation Studio v0.23.0 — bounded WBD/WVD world-resource decoder

This milestone turns the v0.22 byte-preserved world-cell package into a real,
checkable geometry step:

- Every extracted `.wbd` and `.wvd` resource is decompressed from its RSC05
  stream with the bundled Zstandard runtime.
- WBD roots are validated against the RDR1 `BoundsDictionary` VFT. Hash and
  pointer arrays are range-checked, and each pointer-valid bound reports its
  type, flags, bounding box, quantization, source offset, vertex count, and
  polygon-kind histogram.
- WBD polyhedron records recover the quantized vertex arrays and triangle
  indices using the RDR1 Z/X/Y coordinate transform. This is collision/world
  geometry evidence, not a guessed visual mesh.
- WVD roots are validated against the RDR1 `VisualDictionary` VFT. Hashes,
  texture pointers, drawable pointer pages, and drawable counts are exposed.
  Drawable mesh/shader layouts remain explicitly unresolved rather than being
  fabricated from arbitrary bytes.
- Scenes → **Extract world-cell evidence…** now writes these reports beside
  the exact raw inputs. A failed report never deletes or changes the raw
  evidence.

For the bundled Armadillo candidate extracted with `dlc_find_blunderbuss`, the
decoder validated 6 WBD/WVD resources. The two WBD files contain 47 decoded
bounds and 3,637 decoded quantized vertices; the four WVD files contain 31
validated drawable pointers. The candidate cell is still not claimed as the
mission's proven runtime placement.

The next separate milestone is the WVD drawable/fragment mesh layout and
placement-instance reconstruction, followed by a scene adapter that can place
validated geometry in Blender.
