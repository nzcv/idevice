"""Unit tests for :class:`XCDevice` project preparation and build."""

from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from idevice.device import Device, XCDevice, XCDeviceError
from idevice.device.base.errors import CommandExecutionError

_SAMPLE_PBXPROJ = """\
// !$*UTF8*$!
{
\tattributes = {
\t\tTargetAttributes = {
\t\t\tABCDEF1234567890 = {
\t\t\t\tSystemCapabilities = {
\t\t\t\t\tcom.apple.InAppPurchase = {
\t\t\t\t\t\tenabled = 1;
\t\t\t\t\t};
\t\t\t\t};
\t\t\t};
\t\t};
\t};
\tobjects = {
\t\t1111111111111111 /* StoreKit.framework in Frameworks */ = {
\t\t\tisa = PBXBuildFile;
\t\t\tfileRef = 2222222222222222 /* StoreKit.framework */;
\t\t};
\t\t2222222222222222 /* StoreKit.framework */ = {
\t\t\tisa = PBXFileReference;
\t\t\tlastKnownFileType = wrapper.framework;
\t\t\tname = StoreKit.framework;
\t\t\tpath = System/Library/Frameworks/StoreKit.framework;
\t\t\tsourceTree = SDKROOT;
\t\t};
\t};
}
"""


@pytest.fixture
def xcode_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "Unity-iPhone"
    xcodeproj = project_dir / "Unity-iPhone.xcodeproj"
    xcodeproj.mkdir(parents=True)
    (xcodeproj / "project.pbxproj").write_text(_SAMPLE_PBXPROJ, encoding="utf-8")

    entitlements = project_dir / "Unity-iPhone.entitlements"
    entitlements.write_bytes(
        plistlib.dumps(
            {
                "com.apple.developer.in-app-payments": ["merchant.example.app"],
                "com.apple.developer.team-identifier": "TEAMID",
            },
        ),
    )
    return project_dir


def test_prepare_removes_in_app_purchase(xcode_project: Path) -> None:
    device = XCDevice(str(xcode_project))

    changed = device.prepare()

    assert changed is True
    pbxproj = (xcode_project / "Unity-iPhone.xcodeproj" / "project.pbxproj").read_text(
        encoding="utf-8",
    )
    assert "com.apple.InAppPurchase" not in pbxproj
    assert "StoreKit.framework" not in pbxproj

    with (xcode_project / "Unity-iPhone.entitlements").open("rb") as handle:
        entitlements = plistlib.load(handle)
    assert "com.apple.developer.in-app-payments" not in entitlements
    assert entitlements["com.apple.developer.team-identifier"] == "TEAMID"


def test_prepare_is_idempotent(xcode_project: Path) -> None:
    device = XCDevice(str(xcode_project))
    device.prepare()

    assert device.prepare() is False


def test_device_factory_creates_xc_device(xcode_project: Path) -> None:
    device = Device.create(
        "xc",
        device_id=str(xcode_project),
        device_ip="",
    )

    assert isinstance(device, XCDevice)
    assert device.platform == "xc"
    assert device.prepare() is True


def test_build_runs_xcodebuild_and_returns_app(xcode_project: Path) -> None:
    runner = MagicMock()
    app_dir = (
        xcode_project
        / "build"
        / "DerivedData"
        / "Build"
        / "Products"
        / "Release-iphoneos"
    )
    app_dir.mkdir(parents=True)
    app_path = app_dir / "Unity-iPhone.app"
    app_path.mkdir()

    with patch("idevice.device.xc.device.SubprocessRunner", return_value=runner):
        with patch("idevice.device.xc.device.shutil.which", return_value="/usr/bin/xcodebuild"):
            with patch("idevice.device.xc.device.xcodebuild_binary", return_value="xcodebuild"):
                device = XCDevice(str(xcode_project))
                result = device.build()

    assert result == app_path
    runner.run.assert_called_once()
    command = runner.run.call_args.args[0]
    assert command[0] == "xcodebuild"
    assert "-project" in command
    assert str(xcode_project / "Unity-iPhone.xcodeproj") in command
    assert "-scheme" in command
    assert "Unity-iPhone" in command
    assert "-sdk" in command
    assert "iphoneos" in command
    assert runner.run.call_args.kwargs["timeout"] == 3600


def test_build_raises_when_xcodebuild_fails(xcode_project: Path) -> None:
    runner = MagicMock()
    runner.run.side_effect = CommandExecutionError(
        "xcodebuild failed",
        command=["xcodebuild"],
        returncode=65,
        stderr="Signing error",
    )

    with patch("idevice.device.xc.device.SubprocessRunner", return_value=runner):
        with patch("idevice.device.xc.device.shutil.which", return_value="/usr/bin/xcodebuild"):
            with patch("idevice.device.xc.device.xcodebuild_binary", return_value="xcodebuild"):
                device = XCDevice(str(xcode_project))
                with pytest.raises(XCDeviceError, match="xcodebuild failed"):
                    device.build()


def test_build_requires_xcodebuild_on_path(xcode_project: Path) -> None:
    with patch("idevice.device.xc.device.SubprocessRunner"):
        with patch("idevice.device.xc.device.shutil.which", return_value=None):
            with patch("idevice.device.xc.device.xcodebuild_binary", return_value="xcodebuild"):
                device = XCDevice(str(xcode_project))
                with pytest.raises(XCDeviceError, match="xcodebuild"):
                    device.build()


@pytest.fixture
def test_project(tmp_path: Path) -> Path:
    """A fixed, standalone UITest project (decoupled from the app project)."""
    project = tmp_path / "UITests" / "UITests.xcodeproj"
    project.mkdir(parents=True)
    return project


def _patched_runner(runner: MagicMock):
    return (
        patch("idevice.device.xc.device.SubprocessRunner", return_value=runner),
        patch("idevice.device.xc.device.shutil.which", return_value="/usr/bin/xcodebuild"),
        patch("idevice.device.xc.device.xcodebuild_binary", return_value="xcodebuild"),
    )


def test_test_runs_xcodebuild_test_with_udid(
    xcode_project: Path,
    test_project: Path,
) -> None:
    runner = MagicMock()
    runner_patch, which_patch, binary_patch = _patched_runner(runner)

    with runner_patch, which_patch, binary_patch:
        device = XCDevice(str(xcode_project))
        result = device.test(
            test_project=str(test_project),
            scheme="UITests",
            udid="UDID-123",
        )

    runner.run.assert_called_once()
    command = runner.run.call_args.args[0]
    assert command[0] == "xcodebuild"
    assert command[-1] == "test"
    assert "-project" in command
    assert str(test_project) in command
    assert "-scheme" in command
    assert "UITests" in command
    assert "-destination" in command
    assert "platform=iOS,id=UDID-123" in command
    assert "-resultBundlePath" in command
    assert result.suffix == ".xcresult"
    assert runner.run.call_args.kwargs["timeout"] == 3600


def test_test_uses_explicit_destination_and_only_testing(
    xcode_project: Path,
    test_project: Path,
) -> None:
    runner = MagicMock()
    runner_patch, which_patch, binary_patch = _patched_runner(runner)

    with runner_patch, which_patch, binary_patch:
        device = XCDevice(str(xcode_project))
        device.test(
            test_project=str(test_project),
            scheme="UITests",
            destination="platform=iOS Simulator,name=iPhone 15",
            only_testing=["UITests/LoginTests/testHappyPath"],
        )

    command = runner.run.call_args.args[0]
    assert "platform=iOS Simulator,name=iPhone 15" in command
    assert "-only-testing:UITests/LoginTests/testHappyPath" in command


def test_test_uses_workspace_flag(xcode_project: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "UITests" / "UITests.xcworkspace"
    workspace.mkdir(parents=True)
    runner = MagicMock()
    runner_patch, which_patch, binary_patch = _patched_runner(runner)

    with runner_patch, which_patch, binary_patch:
        device = XCDevice(str(xcode_project))
        device.test(test_project=str(workspace), scheme="UITests", udid="UDID-123")

    command = runner.run.call_args.args[0]
    assert "-workspace" in command
    assert "-project" not in command


def test_test_requires_destination(xcode_project: Path, test_project: Path) -> None:
    runner = MagicMock()
    runner_patch, which_patch, binary_patch = _patched_runner(runner)

    with runner_patch, which_patch, binary_patch:
        device = XCDevice(str(xcode_project))
        with pytest.raises(XCDeviceError, match="destination"):
            device.test(test_project=str(test_project), scheme="UITests")
    runner.run.assert_not_called()


def test_test_rejects_non_project_path(xcode_project: Path, tmp_path: Path) -> None:
    bogus = tmp_path / "not-a-project"
    bogus.mkdir()
    runner = MagicMock()
    runner_patch, which_patch, binary_patch = _patched_runner(runner)

    with runner_patch, which_patch, binary_patch:
        device = XCDevice(str(xcode_project))
        with pytest.raises(XCDeviceError, match="xcodeproj or .xcworkspace"):
            device.test(test_project=str(bogus), scheme="UITests", udid="UDID-123")


def test_test_raises_when_xcodebuild_fails(
    xcode_project: Path,
    test_project: Path,
) -> None:
    runner = MagicMock()
    runner.run.side_effect = CommandExecutionError(
        "xcodebuild test failed",
        command=["xcodebuild"],
        returncode=65,
        stderr="Test failure",
    )
    runner_patch, which_patch, binary_patch = _patched_runner(runner)

    with runner_patch, which_patch, binary_patch:
        device = XCDevice(str(xcode_project))
        with pytest.raises(XCDeviceError, match="xcodebuild test failed"):
            device.test(
                test_project=str(test_project),
                scheme="UITests",
                udid="UDID-123",
            )
