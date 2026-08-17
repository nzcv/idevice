"""Unit tests for the ios4-backed ``IOSDevice4`` lifecycle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from idevice.device.base.errors import AppNotInstalledError, DeviceNotFoundError
from idevice.device.base.runner import CommandResult
from idevice.device.ios4.device import IOSDevice4, IOSDevice4Error

APP_ID = "com.example.game"
BINARY = "/opt/ios4"
IDEVICEINSTALLER = "/opt/bin/ideviceinstaller"
UDID = "00000000-0000000000000000"


@pytest.fixture
def ios4_device(tmp_path: Path) -> IOSDevice4:
    """Build an IOSDevice4 with a mocked executable and isolated cache."""
    with patch("idevice.device.ios4.device.ios4_binary", return_value=BINARY):
        with patch(
            "idevice.device.ios4.device.shutil.which", return_value=BINARY
        ):
            device = IOSDevice4(
                UDID,
                device_ip="",
                package_name=APP_ID,
                cache_dir=tmp_path / "cache",
            )
    device._runner = MagicMock()
    return device


def result(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> CommandResult:
    """Create a subprocess result for mocked runner calls."""
    return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)


def test_install_prefers_standalone_ideviceinstaller(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    ipa = tmp_path / "ExampleGame.ipa"
    ipa.write_bytes(b"ipa")
    ios4_device._runner.run.return_value = result(stdout="Install: Complete\n")

    with patch(
        "idevice.device.ios4.device.shutil.which",
        return_value=IDEVICEINSTALLER,
    ):
        assert ios4_device.install(ipa, app_id=APP_ID) is True

    ios4_device._runner.run.assert_called_once_with(
        [IDEVICEINSTALLER, "--udid", UDID, "install", str(ipa)],
        check=False,
        timeout=3600,
    )
    assert ios4_device._app_cache.get(APP_ID) is not None


def test_install_falls_back_to_ios4_when_ideviceinstaller_missing(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    ipa = tmp_path / "ExampleGame.ipa"
    ipa.write_bytes(b"ipa")
    ios4_device._runner.run.return_value = result(stdout="install success\n")

    with patch("idevice.device.ios4.device.shutil.which", return_value=None):
        assert ios4_device.install(ipa, app_id=APP_ID) is True

    ios4_device._runner.run.assert_called_once_with(
        [
            BINARY,
            "--udid",
            UDID,
            "ideviceinstaller",
            "install",
            str(ipa),
        ],
        check=False,
        timeout=3600,
    )
    assert ios4_device._app_cache.get(APP_ID) is not None


def test_install_detects_cli_error_even_with_zero_exit(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    ipa = tmp_path / "ExampleGame.ipa"
    ipa.write_bytes(b"ipa")
    ios4_device._runner.run.return_value = result(
        stderr="Install failed: invalid package\n"
    )

    assert ios4_device.install(ipa, app_id=APP_ID) is False
    assert ios4_device._app_cache.get(APP_ID) is None


def test_install_rejects_missing_package(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError, match="Package not found"):
        ios4_device.install(tmp_path / "missing.ipa", app_id=APP_ID)


def test_application_listing_matches_exact_bundle_id() -> None:
    output = """
Found 2 applications:
  com.example.game        ExampleGame       1.0
  com.example.game.beta   ExampleGame Beta  1.0
"""
    assert IOSDevice4._bundle_id_in_application_listing(output, APP_ID) is True
    assert (
        IOSDevice4._bundle_id_in_application_listing(
            output, "com.example.other"
        )
        is False
    )


def test_launch_passes_environment_and_ordered_arguments(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.side_effect = [
        result(stdout=f"  {APP_ID}  ExampleGame  1.0\n"),
        result(stdout="PID: 4815\n"),
    ]

    ios4_device.launch_app(
        APP_ID,
        args=["--mode", "debug", "--label", "foo,bar", r"C:\tmp"],
        environment={"MallocStackLogging": "1", "FOO": "bar=baz"},
    )

    assert ios4_device.last_launch_pid == 4815
    assert ios4_device._runner.run.call_args_list[0].args[0] == [
        BINARY,
        "--udid",
        UDID,
        "application_listing",
    ]
    assert ios4_device._runner.run.call_args_list[1].args[0] == [
        BINARY,
        "--udid",
        UDID,
        "process_control",
        "--env",
        "MallocStackLogging=1,FOO=bar=baz",
        "--args",
        r"--mode,debug,--label,foo\,bar,C:\\tmp",
        APP_ID,
    ]


def test_launch_without_app_id_uses_bound_package_name(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.side_effect = [
        result(stdout=f"  {APP_ID}  ExampleGame  1.0\n"),
        result(stdout="PID: 4815\n"),
    ]

    ios4_device.launch_app()

    assert ios4_device.last_launch_pid == 4815
    assert ios4_device._runner.run.call_args_list[1].args[0][-1] == APP_ID
    assert ios4_device._last_launch_app_id == APP_ID


def test_launch_without_app_id_or_package_name_raises(
    tmp_path: Path,
) -> None:
    with patch("idevice.device.ios4.device.ios4_binary", return_value=BINARY):
        with patch(
            "idevice.device.ios4.device.shutil.which", return_value=BINARY
        ):
            device = IOSDevice4(UDID, cache_dir=tmp_path / "cache")
    device._runner = MagicMock()

    with pytest.raises(ValueError, match="app_id is required"):
        device.launch_app()

    device._runner.run.assert_not_called()


def test_launch_rejects_uninstalled_app(ios4_device: IOSDevice4) -> None:
    ios4_device._runner.run.return_value = result(
        stdout="Found 0 applications:\n"
    )

    with pytest.raises(AppNotInstalledError, match="App not installed"):
        ios4_device.launch_app(APP_ID)

    assert ios4_device._runner.run.call_count == 1


def test_launch_requires_pid_in_process_control_output(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.side_effect = [
        result(stdout=f"  {APP_ID}  ExampleGame  1.0\n"),
        result(stdout="launch completed without pid\n"),
    ]

    with pytest.raises(IOSDevice4Error, match="did not return a PID"):
        ios4_device.launch_app(APP_ID)


def test_capture_memgraph_uses_last_pid_and_atomically_writes_output(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    output = tmp_path / "snapshots" / "trash-dash.memgraph"
    ios4_device._last_launch_pid = 4815

    def capture(command: list[str], **_kwargs: object) -> CommandResult:
        temporary_path = Path(command[-1])
        temporary_path.write_bytes(b"bplist00-memory-graph")
        return result(stdout=f"Wrote 20 bytes to {temporary_path}\n")

    ios4_device._runner.run.side_effect = capture

    assert ios4_device.capture_memgraph(output) == output.resolve()
    assert output.read_bytes() == b"bplist00-memory-graph"
    command = ios4_device._runner.run.call_args.args[0]
    assert command[:5] == [BINARY, "--udid", UDID, "memgraph", "4815"]
    assert len(command) == 6
    assert Path(command[5]).parent == output.parent.resolve()
    assert ios4_device._runner.run.call_args.kwargs == {
        "check": False,
        "timeout": 600,
    }


def test_capture_memgraph_accepts_explicit_pid(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    output = tmp_path / "snapshot.memgraph"

    def capture(command: list[str], **_kwargs: object) -> CommandResult:
        Path(command[-1]).write_bytes(b"memory-graph")
        return result()

    ios4_device._runner.run.side_effect = capture

    assert ios4_device.capture_memgraph(output, pid=4815) == output.resolve()
    assert output.read_bytes() == b"memory-graph"


def test_capture_memgraph_requires_pid(ios4_device: IOSDevice4, tmp_path: Path) -> None:
    with pytest.raises(IOSDevice4Error, match="No PID available"):
        ios4_device.capture_memgraph(tmp_path / "snapshot.memgraph")


def test_capture_memgraph_failure_preserves_existing_output(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    output = tmp_path / "snapshot.memgraph"
    output.write_bytes(b"previous")
    ios4_device._runner.run.return_value = result(
        stderr="Memory graph capture failed\n"
    )

    with pytest.raises(IOSDevice4Error, match="capture failed"):
        ios4_device.capture_memgraph(output, pid=4815)

    assert output.read_bytes() == b"previous"


def test_stop_pkills_the_bundle_and_clears_the_tracked_pid(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._last_launch_pid = 4815
    ios4_device._last_launch_app_id = APP_ID
    ios4_device._runner.run.return_value = result(stdout="Killed 4815 (Game)\n")

    ios4_device.stop_app()

    ios4_device._runner.run.assert_called_once_with(
        [
            BINARY,
            "--udid",
            UDID,
            "pkill",
            "--bundle",
            APP_ID,
        ],
        check=False,
    )
    assert ios4_device.last_launch_pid is None


def test_stop_keeps_the_tracked_pid_of_another_app(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._last_launch_pid = 4815
    ios4_device._last_launch_app_id = APP_ID
    ios4_device._runner.run.return_value = result(
        stdout="No running process matches com.example.other\n"
    )

    ios4_device.stop_app("com.example.other")

    assert ios4_device.last_launch_pid == 4815


def test_stop_raises_when_pkill_fails(ios4_device: IOSDevice4) -> None:
    ios4_device._runner.run.return_value = result(
        returncode=1, stderr="No installed application with bundle ID\n"
    )

    with pytest.raises(IOSDevice4Error, match="Failed to stop"):
        ios4_device.stop_app()


def test_screenshot_uses_the_ios4_screenshot_service(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    ios4_device._device_ip = "192.0.2.10"
    output = tmp_path / "shots" / "screen.png"

    def capture(command: list[str], **_kwargs: object) -> CommandResult:
        Path(command[-1]).write_bytes(b"\x89PNG")
        return result()

    ios4_device._runner.run.side_effect = capture

    assert ios4_device.screenshot(output) is True

    ios4_device._runner.run.assert_called_once_with(
        [BINARY, "--udid", UDID, "screenshot", str(output)], check=False
    )
    assert output.read_bytes() == b"\x89PNG"


def test_argument_and_environment_validation() -> None:
    assert IOSDevice4._encode_launch_arguments(["foo,bar", r"C:\tmp"]) == (
        r"foo\,bar,C:\\tmp"
    )
    with pytest.raises(ValueError, match="cannot be empty"):
        IOSDevice4._encode_launch_arguments([""])
    with pytest.raises(ValueError, match="cannot contain ','"):
        IOSDevice4._encode_environment({"KEY": "a,b"})


def test_default_udid_parses_ideviceinfo() -> None:
    runner = MagicMock()
    runner.run.return_value = result(
        stdout=f'"UniqueDeviceID": String("{UDID}")\n'
    )
    with patch("idevice.device.ios4.device.ios4_binary", return_value=BINARY):
        with patch(
            "idevice.device.ios4.device.SubprocessRunner", return_value=runner
        ):
            assert IOSDevice4.default_udid() == UDID


def test_default_udid_rejects_unexpected_output() -> None:
    runner = MagicMock()
    runner.run.return_value = result(stdout="no device id\n")
    with patch("idevice.device.ios4.device.ios4_binary", return_value=BINARY):
        with patch(
            "idevice.device.ios4.device.SubprocessRunner", return_value=runner
        ):
            with pytest.raises(DeviceNotFoundError, match="UniqueDeviceID"):
                IOSDevice4.default_udid()
