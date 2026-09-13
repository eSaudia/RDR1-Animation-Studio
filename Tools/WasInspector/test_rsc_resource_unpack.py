import unittest

import zstandard

from rsc_resource_unpack import unpack


class ResourceUnpackTests(unittest.TestCase):
    def test_rsc05_zstandard_payload(self):
        source = (b"RDR1-character-resource" * 64)
        packed = b"RSC\x05" + (138).to_bytes(4, "little") + (0xC0004818).to_bytes(4, "little")
        packed += zstandard.ZstdCompressor().compress(source)
        self.assertEqual(unpack(packed), source)

    def test_rejects_non_resource(self):
        with self.assertRaises(ValueError):
            unpack(b"not an RSC file")


if __name__ == "__main__":
    unittest.main()
