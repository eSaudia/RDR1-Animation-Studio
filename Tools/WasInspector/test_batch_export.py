import unittest

from was_batch_export import public_record, select_records


class BatchExportTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {"name": "walk.anim", "frame_count": 10, "source_path": "a", "duration_seconds": 1,
             "inferred_fps": 10, "track_data_offset_hex": "0x10", "encoded_size": 20},
            {"name": "walk_fast.anim", "frame_count": 8, "source_path": "b", "duration_seconds": 0.5,
             "inferred_fps": 16, "track_data_offset_hex": "0x20", "encoded_size": 30},
            {"name": "idle.anim", "frame_count": 12, "source_path": "c", "duration_seconds": 2,
             "inferred_fps": 6, "track_data_offset_hex": "0x30", "encoded_size": 40},
        ]

    def test_filters_and_limits_catalog(self):
        selected = select_records(self.records, ["WALK"], 1)
        self.assertEqual([item["name"] for item in selected], ["walk.anim"])

    def test_public_record_excludes_internal_offsets(self):
        item = dict(self.records[0], track_data_offset=16)
        self.assertNotIn("track_data_offset", public_record(item))

    def test_exact_name_selection(self):
        selected = select_records(self.records, [], 0, {"idle.anim"})
        self.assertEqual([item["name"] for item in selected], ["idle.anim"])


if __name__ == "__main__":
    unittest.main()
