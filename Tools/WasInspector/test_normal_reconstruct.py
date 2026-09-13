import unittest

from rdr_normal_reconstruct import reconstruct_pixel


class NormalReconstructTests(unittest.TestCase):
    def test_neutral_ag_normal_points_outward(self):
        red, green, blue, alpha = reconstruct_pixel((0, 128, 0, 128))
        self.assertAlmostEqual(red, 128, delta=1)
        self.assertAlmostEqual(green, 127, delta=1)
        self.assertEqual(blue, 255)
        self.assertEqual(alpha, 255)

    def test_out_of_circle_vector_is_normalized(self):
        red, green, blue, _ = reconstruct_pixel((0, 255, 0, 255))
        self.assertGreater(red, 200)
        self.assertLess(green, 55)
        self.assertAlmostEqual(blue, 128, delta=1)


if __name__ == "__main__":
    unittest.main()
