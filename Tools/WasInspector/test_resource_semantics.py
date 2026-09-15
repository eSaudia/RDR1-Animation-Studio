import struct

from resource_semantics import (RDR_OPCODE_NAMES, build_mission_record_index,
                                build_typed_record_candidates,
                                correlate_trace_to_records, decode, decode_vm,
                                load_native_database, load_native_database_metadata,
                                rsc85_layout)


def test_rbf_cutscene_labels_are_semantic():
    data = b"RBF0\x00\x00\x00\x00rage__cutfCutsceneFile2\0" + b"rage__cutfCameraObject\0" + b"pCutsceneEventList\0" + b"rage__cutfObjectIdEvent9\0" * 2
    report = decode(data)
    assert report["format"] == "RBF0"
    assert report["class"] == "rage__cutfCutsceneFile2"
    assert report["semanticCounts"]["objectTypes"] == 4
    assert report["semanticCounts"]["eventRecords"] == 2
    assert report["typedRecordCount"] == 5
    assert report["typedRecordCandidates"][0]["recordType"] == "cutsceneType"
    assert report["typedRecordCandidates"][-1]["recordType"] == "cutsceneEvent"


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
    assert report["nativeUsage"][0]["hash"] == "0x12345678"
    assert report["nativeUsage"][0]["count"] == 1
    assert report["functions"][0]["nativeCallCount"] == 1
    assert report["nativeDomainUsage"] == []


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
    assert report["domainUsage"]["ai"]["callCount"] == 1
    assert report["nativeDomainUsage"][0]["domains"] == ["ai"]
    assert report["functions"][0]["domainUsage"]["ai"]["nativeCallCount"] == 1
    assert report["functionDomainUsage"][0]["functionIndex"] == 0


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
    assert args["signatureStatus"] == "mismatch"
    assert report["signatureMismatchCount"] == 1


def test_vm_reports_bounded_branch_reference():
    header = struct.pack(">11I", 0xA8D74300, 0x5000002C, 0x5000002C, 8, 0, 0, 0, 0x8A019D34, 0, 0, 0)
    payload = header + struct.pack(">I", 0x50000030) + bytes([0x2D, 0, 0, 1, 0x63, 0, 0, 0x2E])
    report = decode_vm(payload)
    assert report["functions"][0]["controlFlowEdges"][0]["kind"] == "JumpFalse"
    assert report["callReferences"][0]["to"] == 55


def test_rsc85_layout_finds_object_start_page():
    resource = b"RSC\x85" + struct.pack("<III", 2, 0x80000000, 0x80000005)
    layout = rsc85_layout(resource)
    assert layout is not None
    assert layout["virtualPageSizes"] == [16384, 4096]
    assert layout["objectStart"] == 16384


def test_resource_script_header_decodes_little_endian_page_and_native_tables():
    data = bytearray(20480)
    object_start = 16384
    # Header mirrors the compact RSC85 script header read by Magic-RDR.
    struct.pack_into("<10I", data, object_start,
                     0x00ACE444, 0x50003F60, 0x50003F80, 8,
                     0, 2, 0x500043D0, 0, 1, 0x50004030)
    struct.pack_into("<I", data, 16256, 0x50000000)
    struct.pack_into("<I", data, 16432, 0x12345678)
    struct.pack_into("<II", data, 17360, 6, 0)
    data[0:8] = bytes([0x2D, 0, 0, 1, 0x8B, 0x2E, 0, 1])
    report = decode_vm(bytes(data), object_start=object_start)
    assert report["headers"][0]["valid"] is True
    assert report["headers"][0]["codeBlockStarts"] == [0]
    assert report["headers"][0]["staticInitialValues"] == [6, 0]
    assert report["headers"][0]["staticNonZeroCount"] == 1
    assert report["nativeTable"][0]["hash"] == "0x12345678"
    assert report["functions"][0]["instructions"][0]["name"] == "Function"


def test_resource_script_discovers_embedded_function_markers_and_extension_opcodes():
    data = bytearray(20480)
    object_start = 16384
    code = bytes([
        0x2D, 0, 0, 1,  # Function(p0, locals=1)
        0x52, 0, 8,      # Call2 -> second Function marker
        0x7A,            # ReturnP0R0
        0x2D, 1, 0, 2,  # Function(p1, locals=2)
        0xAF, 0xB0,     # LoadRef, StoreRef
        0x7E,            # ReturnP1R0
    ])
    struct.pack_into("<10I", data, object_start,
                     0x00ACE444, 0x50003F60, 0x50003F80, len(code),
                     0, 0, 0, 0, 0, 0)
    struct.pack_into("<I", data, 16256, 0x50000000)
    data[:len(code)] = code
    report = decode_vm(bytes(data), object_start=object_start)
    assert report["functionMarkerStarts"] == [0, 8]
    assert report["functionCount"] == 2
    assert report["functions"][1]["terminatedBy"] == "return"
    assert [item["name"] for item in report["functions"][1]["instructions"]] == ["Function", "LoadRef", "StoreRef", "ReturnP1R0"]
    call = report["functions"][0]["instructions"][1]
    assert call["args"]["targetFunctionIndex"] == 1
    assert report["callReferences"][0]["targetFunctionIndex"] == 1
    assert report["callGraphEdgeCount"] == 1
    assert report["callGraphResolvedCallCount"] == 1
    assert report["callGraphUnresolvedCallCount"] == 0
    assert report["callGraph"][0]["callerLabel"] == "main"
    assert report["callGraph"][0]["calleeLabel"] == "Function_1"
    assert report["functions"][0]["outgoingFunctions"] == [1]
    assert report["functions"][1]["incomingFunctions"] == [0]
    trace = report["missionTrace"]
    assert trace["ordering"] == "function index, then instruction offset"
    assert trace["call2Count"] == 1
    assert [event["kind"] for event in trace["events"]] == ["call2", "return", "return"]


def test_resource_identifiers_expose_domain_hints_without_decoding_payload_fields():
    report = decode(b"AttachProp\0GringoCamera\0Gringo_SpeechTime\0")
    hints = report["resourceDomainHints"]
    assert "prop" in hints and "camera" in hints and "audio" in hints and "timing" in hints
    assert hints["prop"]["identifiers"] == ["AttachProp"]
    assert report["missionDomainCorrelation"]["prop"]["resourceIdentifierCount"] == 1
    assert report["typedRecordCount"] == 3
    assert report["missionTraceCorrelation"]["status"] == "resource-candidate-without-trace-domain-evidence"


def test_trace_resource_correlation_is_domain_level_only():
    candidates = build_typed_record_candidates([{"offset": 12, "value": "GringoCamera"}, {"offset": 28, "value": "AttachProp"}])
    trace = {"events": [{"kind": "nativeCall", "domains": ["camera"]}, {"kind": "branch"}]}
    correlation = correlate_trace_to_records(trace, candidates)
    assert correlation["status"] == "matched-domain-evidence"
    assert correlation["matchedDomains"] == ["camera"]
    assert correlation["domains"]["camera"]["matchedEventCount"] == 1
    assert "runtime branch conditions and loop paths" in correlation["unresolved"]


def test_mission_record_index_is_bounded_and_stable():
    candidates = build_typed_record_candidates([{"offset": 12, "value": "GringoCamera"}, {"offset": 28, "value": "AttachProp"}])
    index = build_mission_record_index(candidates, {"events": [{"sequence": 4, "domains": ["camera"]}]})
    assert index["format"] == "RDR mission record index"
    assert index["recordCount"] == 2
    assert index["records"][0]["recordId"] == "resourceIdentifier:12"
    assert index["records"][0]["traceEventSequences"] == [4]
    assert index["records"][1]["traceEventSequences"] == []
    assert index["recordsWithTraceReferences"] == 1


def test_rdr_opcode_table_covers_post_155_extension():
    assert len(RDR_OPCODE_NAMES) == 179
    assert RDR_OPCODE_NAMES[175:179] == ["LoadRef", "StoreRef", "StoreVector", "MakeVector"]
