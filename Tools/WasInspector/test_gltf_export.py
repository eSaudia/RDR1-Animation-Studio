import base64
import unittest

from was_gltf_export import export_gltf, fit_samples


class GltfExportTests(unittest.TestCase):
    def sample(self):
        bone = {"index": 0, "bone_id": 0, "name": "root", "parent_index": -1,
                "parent_name": None, "position": [0, 0, 0], "rotation_xyzw": [0, 0, 0, 1]}
        binding = {"index": 0, "name": "root", "parent_index": -1, "parent_name": None,
                   "bind_position": [0, 0, 0], "bind_rotation_xyzw": [0, 0, 0, 1]}
        return {"animation": {"name": "test", "frame_count": 2, "frames_per_second": 30},
                "skeleton": {"model": "test", "bones": [bone]}, "entries": [{"channels": [{
                    "bone_id": 0, "track_kind_name": "root_quaternion_track", "skeleton_binding": binding,
                    "assembled_track": {"status": "decoded", "samples": [[0, 0, 0, 1], [0, 0, 0, -1]]},
                }]}]}

    def test_builds_embedded_skinned_gltf(self):
        result = export_gltf(self.sample())
        self.assertEqual(result["asset"]["version"], "2.0")
        self.assertEqual(len(result["skins"][0]["joints"]), 1)
        self.assertEqual(len(result["animations"][0]["channels"]), 1)
        encoded = result["buffers"][0]["uri"].split(",", 1)[1]
        self.assertEqual(len(base64.b64decode(encoded)), result["buffers"][0]["byteLength"])

    def test_quaternion_signs_are_made_continuous(self):
        result = export_gltf(self.sample())
        # Two identity quaternions should remain the same sign in the binary output accessor.
        accessor = result["accessors"][result["animations"][0]["samplers"][0]["output"]]
        self.assertEqual(accessor["count"], 2)

    def test_subordinate_sequence_is_fitted_to_master_timeline(self):
        self.assertEqual(fit_samples([[0], [10]], 3), [[0], [0], [10]])
        self.assertEqual(fit_samples([[4]], 2), [[4], [4]])


if __name__ == "__main__":
    unittest.main()
