import pathlib
import struct
import tempfile
import unittest

import was_inspector


class FlagTests(unittest.TestCase):
    def test_bum_virtual_size(self):
        self.assertEqual(was_inspector.decode_rsc5_flags(0xC0000010)["virtual_size"], 4096)

    def test_gent_virtual_size(self):
        self.assertEqual(was_inspector.decode_rsc5_flags(0xC0002B76)["virtual_size"], 7258112)

    def test_rejects_non_rsc5(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bad.was"
            path.write_bytes(b"not an animation resource")
            with self.assertRaises(was_inspector.InspectionError):
                was_inspector.inspect(path)

    def test_detail_limit_preserves_total(self):
        items = [{"n": value} for value in range(5)]
        result = was_inspector.limited(items, 2)
        self.assertEqual(result["count"], 5)
        self.assertEqual(result["items_shown"], 2)
        self.assertEqual(result["items_truncated"], 3)

    def test_zero_detail_limit_means_unlimited(self):
        items = [{"n": value} for value in range(3)]
        result = was_inspector.limited(items, 0)
        self.assertEqual(result["items"], items)

    def test_animation_source_record(self):
        data = bytearray(0x200)
        offset = 0x20
        path_offset = 0x100
        path = b"t:\\RDR2\\assets\\gent_test.anim\x00"
        data[path_offset:path_offset + len(path)] = path
        struct.pack_into("<I", data, offset, was_inspector.ANIMATION_RECORD_VTABLE)
        struct.pack_into("<I", data, offset + 4, 0x9F0)
        struct.pack_into("<HH", data, offset + 8, 46, 45)
        struct.pack_into("<f", data, offset + 0xC, 1.5)
        struct.pack_into("<I", data, offset + 0x10, 0x53797582)
        struct.pack_into("<I", data, offset + 0x14, 0x50000180)
        struct.pack_into("<HH", data, offset + 0x18, 1, 1)
        struct.pack_into("<I", data, offset + 0x1C, 0x50000100)
        struct.pack_into("<IIII", data, offset + 0x20, 0, 0, 0x1234, 2)

        records = was_inspector.scan_animation_source_records(bytes(data))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"], "gent_test")
        self.assertEqual(records[0]["frame_count"], 46)
        self.assertEqual(records[0]["inferred_fps"], 30.0)

    def test_padded_pointer_table_and_sequence(self):
        data = bytearray(0x300)
        struct.pack_into("<I", data, 0x20, 0x50000080)
        data[0x24:0x30] = b"\xCD" * 12
        struct.pack_into("<IIHHIHHI", data, 0x80,
                         0x50000080, 0x50000100, 2, 2, 0x120, 46, 2, 0)
        struct.pack_into("<II", data, 0x100, 0x50000180, 0x500001A0)
        struct.pack_into("<BBH", data, 0x180, 1, 0x11, 0x02AA)
        struct.pack_into("<I", data, 0x184, 0x50000200)
        struct.pack_into("<I", data, 0x180 + 0x14, 1)
        struct.pack_into("<BBH", data, 0x1A0, 0, 0x04, 0)
        struct.pack_into("<III", data, 0x1A4, 0x50000200, 0x50000210, 0x50000220)
        struct.pack_into("<I", data, 0x1A0 + 0x14, 3)

        self.assertEqual(was_inspector.scan_padded_pointer_table(bytes(data), 0x20), [0x80])
        sequence = was_inspector.parse_compressed_sequence(bytes(data), 0x80)
        self.assertIsNotNone(sequence)
        self.assertEqual(sequence["channel_count"], 2)
        self.assertEqual(sequence["frame_count"], 46)
        self.assertEqual(sequence["descriptors"][0]["bone_id"], 0x02AA)
        self.assertEqual(sequence["descriptors"][0]["type_low_nibble"], 1)

    def test_constant_float_payload(self):
        data = bytearray(0x40)
        struct.pack_into("<IIf", data, 0x10, 0x04A21DEC, 0x400, 1.25)
        data[0x1C:0x20] = b"\xCD" * 4
        payload = was_inspector.parse_channel_payload(bytes(data), 0x10)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["class"], "constant_float")
        self.assertEqual(payload["value"], 1.25)

    def test_sampled_payload(self):
        data = bytearray(0x100)
        struct.pack_into(
            "<IIIIIff",
            data,
            0x10,
            0x04A22744,
            0x600,
            0x50000080,
            7,
            46,
            0.125,
            -1.0,
        )
        data[0x2C:0x30] = b"\xCD" * 4
        payload = was_inspector.parse_channel_payload(bytes(data), 0x10)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["class"], "sampled_stream_06")
        self.assertEqual(payload["data_offset"], 0x80)
        self.assertEqual(payload["field_0c"], 7)
        self.assertEqual(payload["field_10"], 46)

    def test_constant_quaternion_reference(self):
        data = bytearray(0x80)
        struct.pack_into("<III", data, 0x10, 0x04A21E78, 0x900, 0x50000040)
        data[0x1C:0x20] = b"\xCD" * 4
        struct.pack_into("<ffff", data, 0x40, 0.0, 0.0, 0.0, 1.0)
        result = was_inspector.decode_payload_samples(bytes(data), 0x10)
        self.assertEqual(result["status"], "decoded")
        self.assertEqual(result["values"], [0.0, 0.0, 0.0, 1.0])

    def test_constant_vector_reference(self):
        data = bytearray(0x80)
        struct.pack_into("<III", data, 0x10, 0x04A21F90, 0xD00, 0x50000040)
        data[0x1C:0x20] = b"\xCD" * 4
        struct.pack_into("<fffI", data, 0x40, 1.0, 2.0, 3.0, 0x7FC00001)
        result = was_inspector.decode_payload_samples(bytes(data), 0x10)
        self.assertEqual(result["status"], "decoded")
        self.assertEqual(result["values"], [1.0, 2.0, 3.0])
        self.assertEqual(result["sentinel_hex"], "0x7FC00001")


if __name__ == "__main__":
    unittest.main()
