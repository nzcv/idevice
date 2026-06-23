"""Unit tests for :meth:`XCDevice.upsert_testing_bundle` (doc steps 1-3)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from idevice.device import Device, UpsertTestingBundle, XCDevice, XCDeviceError

_APP_TARGET_ID = "AAAAAAAAAAAAAAAAAAAAAAAA"
_PROJECT_ID = "BBBBBBBBBBBBBBBBBBBBBBBB"
_APP_PRODUCT_ID = "CCCCCCCCCCCCCCCCCCCCCCCC"

_SAMPLE_PBXPROJ = f"""\
// !$*UTF8*$!
{{
\tarchiveVersion = 1;
\tobjectVersion = 56;
\tobjects = {{
\t\t{_APP_PRODUCT_ID} /* Unity-iPhone.app */ = {{
\t\t\tisa = PBXFileReference;
\t\t\texplicitFileType = wrapper.application;
\t\t\tincludeInIndex = 0;
\t\t\tpath = "Unity-iPhone.app";
\t\t\tsourceTree = BUILT_PRODUCTS_DIR;
\t\t}};
\t\tDDDDDDDDDDDDDDDDDDDDDDDD /* Products */ = {{
\t\t\tisa = PBXGroup;
\t\t\tchildren = (
\t\t\t\t{_APP_PRODUCT_ID} /* Unity-iPhone.app */,
\t\t\t);
\t\t\tname = Products;
\t\t\tsourceTree = "<group>";
\t\t}};
\t\t{_APP_TARGET_ID} /* Unity-iPhone */ = {{
\t\t\tisa = PBXNativeTarget;
\t\t\tbuildConfigurationList = EEEEEEEEEEEEEEEEEEEEEEEE /* Build configuration list */;
\t\t\tbuildPhases = (
\t\t\t);
\t\t\tdependencies = (
\t\t\t);
\t\t\tname = "Unity-iPhone";
\t\t\tproductName = "Unity-iPhone";
\t\t\tproductReference = {_APP_PRODUCT_ID} /* Unity-iPhone.app */;
\t\t\tproductType = "com.apple.product-type.application";
\t\t}};
\t\t{_PROJECT_ID} /* Project object */ = {{
\t\t\tisa = PBXProject;
\t\t\tattributes = {{
\t\t\t\tTargetAttributes = {{
\t\t\t\t\t{_APP_TARGET_ID} = {{
\t\t\t\t\t\tCreatedOnToolsVersion = 15.0;
\t\t\t\t\t}};
\t\t\t\t}};
\t\t\t}};
\t\t\ttargets = (
\t\t\t\t{_APP_TARGET_ID} /* Unity-iPhone */,
\t\t\t);
\t\t}};
\t}};
\trootObject = {_PROJECT_ID} /* Project object */;
}}
"""


@pytest.fixture
def xcode_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "Unity-iPhone"
    xcodeproj = project_dir / "Unity-iPhone.xcodeproj"
    xcodeproj.mkdir(parents=True)
    (xcodeproj / "project.pbxproj").write_text(_SAMPLE_PBXPROJ, encoding="utf-8")
    return project_dir


def _pbxproj_text(project_dir: Path) -> str:
    return (project_dir / "Unity-iPhone.xcodeproj" / "project.pbxproj").read_text(
        encoding="utf-8",
    )


def test_xcdevice_implements_interface(xcode_project: Path) -> None:
    device = XCDevice(str(xcode_project))
    assert isinstance(device, UpsertTestingBundle)


def test_upsert_adds_target_source_and_scheme(xcode_project: Path) -> None:
    device = XCDevice(str(xcode_project))

    changed = device.upsert_testing_bundle()

    assert changed is True
    pbxproj = _pbxproj_text(xcode_project)
    assert "/* UnityMemoryUITests */ = {" in pbxproj
    assert 'productType = "com.apple.product-type.bundle.ui-testing"' in pbxproj
    assert 'TEST_TARGET_NAME = "Unity-iPhone"' in pbxproj
    assert "UnityMemoryUITests.xctest" in pbxproj

    target_id = pbxproj.split(" /* UnityMemoryUITests */ = {\n\t\t\tisa = PBXNativeTarget;")[0][-24:]
    targets_section = pbxproj.split("targets = (", 1)[1].split(");", 1)[0]
    assert target_id in targets_section

    source = xcode_project / "UnityMemoryUITests" / "UnityMemoryUITests.swift"
    assert source.is_file()
    body = source.read_text(encoding="utf-8")
    assert "XCTMemoryMetric(application: app)" in body
    assert "startMeasuring()" in body
    assert "func testGameplayMemory()" in body

    scheme = (
        xcode_project
        / "Unity-iPhone.xcodeproj"
        / "xcshareddata"
        / "xcschemes"
        / "Unity-iPhone.xcscheme"
    )
    assert scheme.is_file()
    root = ET.fromstring(scheme.read_text(encoding="utf-8"))
    testables = root.findall(".//Testables/TestableReference/BuildableReference")
    assert any(ref.get("BuildableName") == "UnityMemoryUITests.xctest" for ref in testables)


def test_upsert_is_idempotent(xcode_project: Path) -> None:
    device = XCDevice(str(xcode_project))
    assert device.upsert_testing_bundle() is True

    pbxproj_after_first = _pbxproj_text(xcode_project)

    assert device.upsert_testing_bundle() is False
    assert _pbxproj_text(xcode_project) == pbxproj_after_first
    assert pbxproj_after_first.count('productType = "com.apple.product-type.bundle.ui-testing"') == 1


def test_upsert_patches_existing_scheme(xcode_project: Path) -> None:
    schemes_dir = xcode_project / "Unity-iPhone.xcodeproj" / "xcshareddata" / "xcschemes"
    schemes_dir.mkdir(parents=True)
    existing = """<?xml version="1.0" encoding="UTF-8"?>
<Scheme version = "1.7">
   <TestAction buildConfiguration = "Debug">
      <Testables>
      </Testables>
   </TestAction>
</Scheme>
"""
    (schemes_dir / "Unity-iPhone.xcscheme").write_text(existing, encoding="utf-8")

    device = XCDevice(str(xcode_project))
    assert device.upsert_testing_bundle() is True

    root = ET.fromstring((schemes_dir / "Unity-iPhone.xcscheme").read_text(encoding="utf-8"))
    refs = root.findall(".//Testables/TestableReference/BuildableReference")
    assert len(refs) == 1
    assert refs[0].get("BuildableName") == "UnityMemoryUITests.xctest"


def test_upsert_custom_names_and_scheme(xcode_project: Path) -> None:
    device = XCDevice(str(xcode_project))

    changed = device.upsert_testing_bundle(
        bundle_name="MemTests",
        test_class="MemTests",
        test_method="testHeap",
        scheme="CustomScheme",
    )

    assert changed is True
    pbxproj = _pbxproj_text(xcode_project)
    assert "/* MemTests */ = {" in pbxproj
    source = xcode_project / "MemTests" / "MemTests.swift"
    assert "func testHeap()" in source.read_text(encoding="utf-8")
    scheme = (
        xcode_project
        / "Unity-iPhone.xcodeproj"
        / "xcshareddata"
        / "xcschemes"
        / "CustomScheme.xcscheme"
    )
    assert scheme.is_file()


def test_upsert_unknown_app_target_raises(xcode_project: Path) -> None:
    device = XCDevice(str(xcode_project))
    with pytest.raises(XCDeviceError, match="DoesNotExist"):
        device.upsert_testing_bundle(app_target="DoesNotExist")


def test_factory_device_upserts(xcode_project: Path) -> None:
    device = Device.create("xc", device_id=str(xcode_project), device_ip="")
    assert device.upsert_testing_bundle() is True
