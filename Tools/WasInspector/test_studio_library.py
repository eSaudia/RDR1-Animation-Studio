import copy
import unittest
from studio_library import validate_motion

def fixture():
    return {'animation':{'name':'walk','frame_count':2,'frames_per_second':30},'entries':[{'kind':'compressed_sequence','frame_count':2,'channels':[{'bone_id':1,'track_kind':0,'assembled_track':{'status':'decoded','samples':[[0,0,0],[1,0,0]]}}]}]}

class CatalogTests(unittest.TestCase):
    def test_same_name_different_motion_is_not_duplicate(self):
        a=fixture(); b=copy.deepcopy(a);b['entries'][0]['channels'][0]['assembled_track']['samples'][1][0]=2
        self.assertNotEqual(validate_motion(a),validate_motion(b))
    def test_different_names_same_motion_are_duplicate(self):
        a=fixture();b=copy.deepcopy(a);b['animation']['name']='alias'
        self.assertEqual(validate_motion(a),validate_motion(b))
    def test_timing_is_part_of_identity(self):
        a=fixture();b=copy.deepcopy(a);b['animation']['frames_per_second']=60
        self.assertNotEqual(validate_motion(a),validate_motion(b))
    def test_no_stretching_short_sequences(self):
        a=fixture();a['animation']['frame_count']=3
        with self.assertRaisesRegex(ValueError,'timing'):validate_motion(a)
    def test_no_silent_replacement_of_repeated_bone_track(self):
        a=fixture();a['entries'][0]['channels']*=2
        with self.assertRaisesRegex(ValueError,'Ambiguous'):validate_motion(a)
    def test_reject_undecoded_or_partial_tracks(self):
        for changes in ({'status':'unsupported_layout'},{'samples_truncated':1}):
            a=fixture();a['entries'][0]['channels'][0]['assembled_track'].update(changes)
            with self.assertRaises(ValueError):validate_motion(a)

if __name__=='__main__':unittest.main()
