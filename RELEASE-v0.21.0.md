# RDR1 Animation Studio v0.21.0 — original-audio mission showcase

This milestone adds the first reproducible mission vertical slice:

- Scenes can build a `DLC_Find_Blunderbuss` showcase from the scanned game folder.
- The decoded `gent_nor_hnd_knl_bbuss` John motion and interaction definition are staged in a Blender scene.
- `blunderbuss.awc` is extracted from the installed game and its embedded RIFF/WAVE members are preserved byte-for-byte.
- The scene embeds original AWC audio and includes an extraction manifest with codec/sample-rate/block-alignment evidence.
- The scene and MP4 manifest explicitly distinguish original game audio from presentation-proxy workshop geometry.

The streamed original world, collision/terrain, mission prop transforms, and runtime camera transform payload are still unresolved. They are not silently replaced with a claim of fidelity.
