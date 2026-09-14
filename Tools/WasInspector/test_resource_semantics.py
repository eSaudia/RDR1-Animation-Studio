import struct

from resource_semantics import decode, decode_vm, load_native_database, load_native_database_metadata


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


def test_vm_resolves_native_name_from_database(tmp_path):
    database = tmp_path / "natives.json"
    database.write_text('{"natives": [{"hash": "0x12345678", "name": "TASK_MOVE"}]}', encoding="utf-8")
    names, selected = load_native_database(database)
    assert selected == str(database)
    assert names[0x12345678] == "TASK_MOVE"
    header = struct.pack(">11I", 0xA8D74300, 0x5000002C, 0x5000002C, 3, 0, 0, 0, 0x8A019D34, 1, 0x50000034, 0)
    payload = header + struct.pack(">I", 0x50000030) + bytes([0x2C, 0, 3]) + b"\0" + struct.pack(">I", 0x12345678)
    report = decode_vm(payload, native_names=names)
    call = report["functions"][0]["instructions"][0]
    assert call["args"]["nativeName"] == "TASK_MOVE"
    assert report["nativeTable"][0]["name"] == "TASK_MOVE"


def test_native_database_accepts_hash_to_name_object(tmp_path):
    database = tmp_path / "natives.json"
    database.write_text('{"0xABCDEF01": "AUDIO_PLAY"}', encoding="utf-8")
    names, _ = load_native_database(database)
    assert names == {0xABCDEF01: "AUDIO_PLAY"}


def test_native_database_accepts_category_hash_records(tmp_path):
    database = tmp_path / "natives.json"
    database.write_text('{"ACTOR": {"0x2D54B916": {"name": "TELEPORT_ACTOR", "return_type": "void"}}}', encoding="utf-8")
    names, _ = load_native_database(database)
    assert names[0x2D54B916] == "TELEPORT_ACTOR"


def test_native_metadata_adds_signature_to_call(tmp_path):
    database = tmp_path / "natives.json"
    database.write_text('{"ACTOR": {"0x12345678": {"name": "TASK_MOVE", "params": [{"type": "Actor"}, {"type": "Vector3*"}], "return_type": "BOOL"}}}', encoding="utf-8")
    metadata, _ = load_native_database_metadata(database)
    names = {native_hash: item["name"] for native_hash, item in metadata.items()}
    header = struct.pack(">11I", 0xA8D74300, 0x5000002C, 0x5000002C, 3, 0, 0, 0, 0x8A019D34, 1, 0x50000034, 0)
    payload = header + struct.pack(">I", 0x50000030) + bytes([0x2C, 0, 3]) + b"\0" + struct.pack(">I", 0x12345678)
    report = decode_vm(payload, native_names=names, native_metadata=metadata)
    args = report["functions"][0]["instructions"][0]["args"]
    assert args["nativeParameterTypes"] == ["Actor", "Vector3*"]
    assert args["nativeReturnType"] == "BOOL"
