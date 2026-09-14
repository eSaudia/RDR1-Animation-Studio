from resource_semantics import decode


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
