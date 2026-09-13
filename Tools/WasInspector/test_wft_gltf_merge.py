import base64
import tempfile
import unittest
from pathlib import Path

from was_gltf_export import export_gltf
from wft_gltf_merge import merge


class WftGltfMergeTests(unittest.TestCase):
    def test_embeds_textures_and_preserves_shader_mapping(self):
        bone = {"index": 0, "name": "root", "parent_index": -1,
                "position": [0, 0, 0], "rotation_xyzw": [0, 0, 0, 1]}
        animation = {"animation": {"name": "test", "frame_count": 1, "frames_per_second": 30},
                     "skeleton": {"model": "test", "bones": [bone]}, "entries": []}
        geometry = {"geometries": [{
            "index": 0, "vertex_count": 3, "face_count": 1,
            "positions": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            "normals": [[0, 0, 1]] * 3, "texcoords": [[0, 0], [1, 0], [0, 1]],
            "colors": [[1, 1, 1, 0]] * 3, "tangents": [],
            "blend_indices": [[0, 0, 0, 0]] * 3,
            "blend_weights": [[1, 0, 0, 0]] * 3,
            "indices": [0, 1, 2], "shader_id": 7,
            "shader_textures": ["body_alphaKill_d1.dds", "body_so1.dds", "body_n1.dds"],
        }]}
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "body_alphakill_d1.png").write_bytes(tiny_png)
            (root / "body_n1.png").write_bytes(tiny_png)
            result = merge(export_gltf(animation), geometry, [root],
                           standard_normal_maps=True, vertex_colors=True)

        self.assertEqual(result["extras"]["wftMaterialCount"], 1)
        self.assertEqual(result["extras"]["wftEmbeddedTextureCount"], 2)
        self.assertEqual(result["materials"][0]["alphaMode"], "MASK")
        primitive = result["meshes"][0]["primitives"][0]
        self.assertEqual(primitive["extras"]["sourceShader"], 7)
        color_accessor = result["accessors"][primitive["attributes"]["COLOR_0"]]
        self.assertEqual(color_accessor["type"], "VEC3")
        self.assertIn("baseColorTexture", result["materials"][0]["pbrMetallicRoughness"])
        self.assertIn("normalTexture", result["materials"][0])


if __name__ == "__main__":
    unittest.main()
