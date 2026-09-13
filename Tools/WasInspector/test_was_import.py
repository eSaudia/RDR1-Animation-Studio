import pathlib
import struct
import tempfile
import unittest
from was_import import import_samples
from was_inspector import load_rsc5

class ImportTests(unittest.TestCase):
    def fixture(self, folder, shared=False):
        d=bytearray(1024)
        for offset in ([0x20,0x60] if shared else [0x20]):
            struct.pack_into('<IIHHfIIHHIIIII',d,offset,0x04A1F634,0x9f0,2,1,1/30,0,0x50000180,1,1,0x50000100,0,0,0x100,2)
        d[0x100:0x10a]=b'test.anim\0'
        struct.pack_into('<I',d,0x180,0x50000200);d[0x184:0x190]=b'\xcd'*12
        struct.pack_into('<IIHHIHHI',d,0x200,0x50000200,0x50000240,1,1,0x100,2,2,0)
        struct.pack_into('<I',d,0x240,0x50000280)
        struct.pack_into('<BBHIIII I',d,0x280,0,0x04,1,0x50000300,0,0,0,1)
        struct.pack_into('<III',d,0x300,0x04A21F90,0xd00,0x50000340)
        struct.pack_into('<fffI',d,0x340,1,2,3,0x7fc00001)
        source=pathlib.Path(folder)/'source.was';source.write_bytes(b'RSC\x05'+struct.pack('<II',6,0x80000004)+d)
        bones=[{'bone_id':1,'name':'root','position':[1,2,3],'rotation_xyzw':[0,0,0,1]}]
        return source,bones
    def test_constant_edit_decodes_and_preserves_size(self):
        with tempfile.TemporaryDirectory() as f:
            source,bones=self.fixture(f);out=pathlib.Path(f)/'new.was'
            report=import_samples(source,0x20,{'Frames':2,'Tracks':[{'name':'root','path':'translation','samples':[[4,2,3],[4,2,3]]}]},bones,out)
            self.assertTrue(report['changed_ranges']);self.assertEqual(out.stat().st_size,source.stat().st_size)
            self.assertEqual(struct.unpack_from('<f',load_rsc5(out)[-1],0x340)[0],4)
    def test_shared_track_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as f:
            source,bones=self.fixture(f,True);out=pathlib.Path(f)/'new.was'
            with self.assertRaisesRegex(ValueError,'shared'):
                import_samples(source,0x20,{'Frames':2,'Tracks':[{'name':'root','path':'translation','samples':[[4,2,3],[4,2,3]]}]},bones,out)
            self.assertFalse(out.exists())
    def test_constant_to_animated_rejected(self):
        with tempfile.TemporaryDirectory() as f:
            source,bones=self.fixture(f)
            with self.assertRaisesRegex(ValueError,'Constant'):
                import_samples(source,0x20,{'Frames':2,'Tracks':[{'name':'root','path':'translation','samples':[[4,2,3],[5,2,3]]}]},bones,pathlib.Path(f)/'new.was')
    def test_wrong_duration_rejected(self):
        with tempfile.TemporaryDirectory() as f:
            source,bones=self.fixture(f)
            with self.assertRaisesRegex(ValueError,'length'):
                import_samples(source,0x20,{'Frames':3,'Tracks':[]},bones,pathlib.Path(f)/'new.was')

if __name__=='__main__':unittest.main()
