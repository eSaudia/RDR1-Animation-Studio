import pathlib
import random
import tempfile
import unittest

from was_roundtrip import pack_codes, unpack_codes, rebuild
from was_inspector import InspectionError


class RoundTripTests(unittest.TestCase):
    def test_known_little_endian_encoding_and_padding(self):
        self.assertEqual(pack_codes([1, 2, 3], 3, b'\x00\xfe'), b'\xd1\xfe')
        self.assertEqual(unpack_codes(b'\xd1\xfe', 0, 3, 3), [1, 2, 3])

    def test_all_widths_and_non_byte_aligned_counts(self):
        rng = random.Random(723)
        for width in range(1, 33):
            for count in (1, 3, 8, 19, 97):
                original = rng.randbytes((width * count + 7) // 8)
                self.assertEqual(pack_codes(unpack_codes(original, 0, width, count), width, original), original)

    def test_reject_out_of_range_codes(self):
        for codes in ([-1], [8], [1.5], [True]):
            with self.assertRaises(InspectionError):
                pack_codes(codes, 3, b'\x00')

    def test_reject_invalid_bounds(self):
        for args in ((b'\0', -1, 1, 1), (b'\0', 0, 32, 1), (b'\0', 0, 0, 1)):
            with self.assertRaises(InspectionError):
                unpack_codes(*args)

    def test_refuse_source_and_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source = pathlib.Path(folder) / 'source.was'
            source.write_bytes(b'original')
            with self.assertRaises(InspectionError):
                rebuild(source, source)
            output = pathlib.Path(folder) / 'output.was'
            output.write_bytes(b'keep')
            with self.assertRaises(InspectionError):
                rebuild(source, output)
            self.assertEqual(output.read_bytes(), b'keep')
            self.assertEqual(source.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
