import struct
import unittest

from wft_geometry_inspector import dec3n, half_be


class WftGeometryTests(unittest.TestCase):
    def test_half_float_is_big_endian_gpu_data(self):
        self.assertEqual(half_be(struct.pack(">e", 1.5)), 1.5)

    def test_dec3n_axes(self):
        self.assertAlmostEqual(dec3n(511)[0], 1.0)
        self.assertAlmostEqual(dec3n(0x200)[0], -1.0)


if __name__ == "__main__":
    unittest.main()
