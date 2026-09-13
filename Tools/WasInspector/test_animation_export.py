import unittest
import math

import was_animation_export
import was_inspector


class AnimationExportTests(unittest.TestCase):
    def test_lsb_quantized_samples(self):
        values = [1, 2, 3, 7, 0]
        bits = 3
        packed = sum(value << (index * bits) for index, value in enumerate(values))
        data = packed.to_bytes((len(values) * bits + 7) // 8, "little")
        decoded = was_inspector.unpack_quantized_samples(
            data, 0, bits, len(values), 0.5, -1.0
        )
        self.assertEqual(decoded, [-0.5, 0.0, 0.5, 2.5, -1.0])

    def test_select_record_exact_before_partial(self):
        records = [{"name": "walk"}, {"name": "walk_fast"}]
        self.assertEqual(was_animation_export.select_record(records, "walk")["name"], "walk")

    def test_select_record_rejects_ambiguous_fragment(self):
        records = [{"name": "walk_left"}, {"name": "walk_right"}]
        with self.assertRaises(was_inspector.InspectionError):
            was_animation_export.select_record(records, "walk")

    def test_assemble_vector_curves(self):
        payloads = [
            {"class": "constant_float", "values": [1.0]},
            {"class": "sampled_stream_06", "values": [2.0, 3.0]},
            {"class": "constant_float", "values": [4.0]},
        ]
        result = was_animation_export.assemble_track_samples("vector_track", payloads, 2)
        self.assertEqual(result["samples"], [[1.0, 2.0, 4.0], [1.0, 3.0, 4.0]])

    def test_reconstruct_packed_quaternion(self):
        payloads = [{
            "class": "component_stream_0e",
            "axes": [
                {"values": [0.0, 0.6]},
                {"values": [0.0]},
                {"values": [0.0]},
            ],
        }]
        result = was_animation_export.assemble_track_samples("quaternion_track", payloads, 2)
        self.assertEqual(result["format"], "xyzw")
        self.assertAlmostEqual(result["samples"][0][3], 1.0)
        self.assertAlmostEqual(result["samples"][1][3], 0.8)
        self.assertTrue(all(abs(math.sqrt(sum(v * v for v in sample)) - 1.0) < 1e-6 for sample in result["samples"]))


if __name__ == "__main__":
    unittest.main()
