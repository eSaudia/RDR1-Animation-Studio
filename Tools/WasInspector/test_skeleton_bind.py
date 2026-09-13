import unittest

from was_skeleton_bind import bind


class SkeletonBindTests(unittest.TestCase):
    def test_binds_names_and_reports_complete_coverage(self):
        animation = {"entries": [{"channels": [{"bone_id": 7}]}]}
        bones = [{
            "index": 1, "bone_id": 7, "name": "head", "parent_index": 0,
            "parent_name": "neck", "position": [1.0, 2.0, 3.0],
            "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
        }]
        result = bind(animation, "test", bones)
        self.assertEqual(result["entries"][0]["channels"][0]["skeleton_binding"]["name"], "head")
        self.assertTrue(result["binding_coverage"]["complete"])

    def test_reports_unknown_ids(self):
        animation = {"entries": [{"channels": [{"bone_id": 8}]}]}
        result = bind(animation, None, [])
        self.assertEqual(result["binding_coverage"]["unmatched_bone_ids"], [8])
        self.assertFalse(result["binding_coverage"]["complete"])


if __name__ == "__main__":
    unittest.main()
