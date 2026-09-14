import struct

from resource_semantics import decode, decode_vm


def test_rbf_cutscene_labels_are_semantic():
    data = b"RBF0\x00\x00\x00\x00rage__cutfCutsceneFile2\0" + b"rage__cutfCameraObject\0" + b"pCutsceneEventList\0" + b"rage__cutfObjectIdEvent9\0" * 2
    report = decode(data)
    assert report["format"] == "RBF0"
    assert report["class"] == "rage__cutfCutsceneFile2"
    assert report["semanticCounts"]["objectTypes"] == 4
    assert report["semanticCounts"]["eventRecords"] == 2


def test_camera_xml_attributes_are_typed():
    report = decode(b'<default><RoamCam><CraneTrackingRate value="3.0"/><EnableAuto value="true"/></RoamCam></default>')
    assert report["format"] == "XML"
    attrs = report["elements"][2]["attributes"]
    assert attrs["value"] == 3.0
    assert report["elements"][3]["attributes"]["value"] is True


def test_zstd_without_expansion_is_identified():
    report = decode(b"\x28\xb5\x2f\xfd" + b"x" * 16)
    assert report["format"] == "Zstandard"


def test_vm_requires_script_header_and_decodes_validated_sizes():
    # RDR header + one code-block pointer + Function (0x2D, 4 bytes) + Return.
    header = struct.pack(">11I", 0xA8D74300, 0x5000002C, 0x5000002C, 7, 1, 0, 0, 0x8A019D34, 0, 0, 0)
    payload = header + struct.pack(">I", 0x50000030) + bytes([0x2D, 1, 0, 2, 0x2E, 0, 1, 0x8B])
    report = decode_vm(payload)
    assert report["headerCandidates"] == [0]
    assert report["headers"][0]["valid"] is True
    assert report["functions"][0]["instructions"][0]["name"] == "Function"
    assert report["functions"][0]["instructions"][0]["bytes"] == "2d010002"


def test_vm_resolves_native_table_hashes_without_names():
    header = struct.pack(">11I", 0xA8D74300, 0x5000002C, 0x5000002C, 3, 0, 0, 0, 0x8A019D34, 1, 0x50000034, 0)
    payload = header + struct.pack(">I", 0x50000030) + bytes([0x2C, 0, 3]) + b"\0" + struct.pack(">I", 0x12345678)
    report = decode_vm(payload)
    call = report["functions"][0]["instructions"][0]
    assert call["name"] == "CallNative"
    assert call["args"]["nativeHash"] == "0x12345678"
    assert call["args"]["parameterCount"] == 1
    assert call["args"]["returnsValue"] is True
