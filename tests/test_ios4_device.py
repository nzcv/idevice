"""Unit tests for the ios4-backed ``IOSDevice4`` lifecycle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from idevice.device.base.errors import AppNotInstalledError, DeviceNotFoundError
from idevice.device.base.runner import CommandResult
from idevice.device.common.ios4cli import IOS4CLI
from idevice.device.common.iwda2 import IWDA2Error, IWDA2Mixin
from idevice.device.ios4.device import IOSDevice4, IOSDevice4Error

APP_ID = "com.example.game"
BINARY = "/opt/ios4"
IDEVICEINSTALLER = "/opt/bin/ideviceinstaller"
UDID = "00000000-0000000000000000"
DEVICE_IP = "192.0.2.10"


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


def test_ios4_device_composes_the_common_ios4cli(
    ios4_device: IOSDevice4,
) -> None:
    assert isinstance(ios4_device._ios4cli, IOS4CLI)
    assert not issubclass(IOS4CLI, IOSDevice4)
    assert ios4_device._runner is ios4_device._ios4cli.runner
    assert not hasattr(ios4_device._ios4cli, "app_cache")


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


def test_launch_uses_process_control(
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
    assert ios4_device._last_launch_app_id == APP_ID
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


def test_launch_validates_encoding_before_process_control(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.return_value = result(
        stdout=f"  {APP_ID}  ExampleGame  1.0\n"
    )

    with pytest.raises(ValueError, match="cannot be empty"):
        ios4_device.launch_app(APP_ID, args=["--mode", ""])

    assert ios4_device._runner.run.call_count == 1
    assert ios4_device._runner.run.call_args.args[0] == [
        BINARY,
        "--udid",
        UDID,
        "application_listing",
    ]


def test_launch_requires_pid_in_process_control_output(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.side_effect = [
        result(stdout=f"  {APP_ID}  ExampleGame  1.0\n"),
        result(stdout="launch completed without pid\n"),
    ]

    with pytest.raises(IOSDevice4Error, match="did not return a PID"):
        ios4_device.launch_app(APP_ID)


def test_native_launch_uses_process_control(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._last_launch_pid = 4815

    ios4_device.launch(APP_ID)

    ios4_device._runner.run.assert_called_once_with(
        [BINARY, "--udid", UDID, "process_control", APP_ID]
    )
    # launch() does not read back a PID, so the one recorded by an earlier
    # launch_app() survives; capture_memgraph() needs an explicit pid here.
    assert ios4_device.last_launch_pid == 4815


def test_native_launch_uses_bound_package_name(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device.launch()

    assert ios4_device._runner.run.call_args.args[0][-1] == APP_ID


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


def test_stop_uses_pkill_and_clears_the_tracked_pid(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._last_launch_pid = 4815
    ios4_device._last_launch_app_id = APP_ID
    ios4_device._runner.run.return_value = result(stdout="Killed 4815 (Game)\n")

    ios4_device.stop_app()

    ios4_device._runner.run.assert_called_once_with(
        [BINARY, "--udid", UDID, "pkill", "--bundle", APP_ID],
        check=False,
    )
    assert ios4_device.last_launch_pid is None


def test_stop_keeps_the_tracked_pid_of_another_app(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._last_launch_pid = 4815
    ios4_device._last_launch_app_id = APP_ID
    ios4_device._runner.run.return_value = result(stdout="Killed 99 (Other)\n")

    ios4_device.stop_app("com.example.other")

    ios4_device._runner.run.assert_called_once_with(
        [BINARY, "--udid", UDID, "pkill", "--bundle", "com.example.other"],
        check=False,
    )
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


def test_ios4_reuses_shared_iwda2_methods() -> None:
    assert IOSDevice4.tap is IWDA2Mixin.tap
    assert IOSDevice4.start_moniter is IWDA2Mixin.start_moniter
    assert IOSDevice4.stop_moniter is IWDA2Mixin.stop_moniter


def test_tap_calls_iwda2_with_normalized_coordinates_and_bundle_id(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._device_ip = DEVICE_IP
    response = MagicMock(status_code=200)

    with patch(
        "idevice.device.common.iwda2.requests.get", return_value=response
    ) as get:
        ios4_device.tap(0.25, 0.75, app_id="com.example.foreground")

    get.assert_called_once_with(
        f"http://{DEVICE_IP}:18201/api/tap",
        params={
            "x": 0.25,
            "y": 0.75,
            "bundleId": "com.example.foreground",
        },
        timeout=30.0,
    )


def test_tap_uses_the_bound_package_name_as_the_iwda2_anchor(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._device_ip = DEVICE_IP
    response = MagicMock(status_code=200)

    with patch(
        "idevice.device.common.iwda2.requests.get", return_value=response
    ) as get:
        ios4_device.tap(0, 1)

    assert get.call_args.kwargs["params"] == {
        "x": 0.0,
        "y": 1.0,
        "bundleId": APP_ID,
    }


@pytest.mark.parametrize(
    ("x", "y", "coordinate"),
    [
        (-0.1, 0.5, "x"),
        (1.1, 0.5, "x"),
        (0.5, -0.1, "y"),
        (0.5, 1.1, "y"),
        (True, 0.5, "x"),
        (float("nan"), 0.5, "x"),
        (0.5, float("inf"), "y"),
    ],
)
def test_tap_rejects_invalid_normalized_coordinates(
    ios4_device: IOSDevice4,
    x: float,
    y: float,
    coordinate: str,
) -> None:
    ios4_device._device_ip = DEVICE_IP
    with patch("idevice.device.common.iwda2.requests.get") as get:
        with pytest.raises(
            ValueError, match=rf"{coordinate} must be a normalized coordinate"
        ):
            ios4_device.tap(x, y)

    get.assert_not_called()


def test_tap_raises_when_iwda2_is_unreachable(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._device_ip = DEVICE_IP
    with patch(
        "idevice.device.common.iwda2.requests.get",
        side_effect=requests.ConnectionError("refused"),
    ):
        with pytest.raises(IWDA2Error, match="tap request failed"):
            ios4_device.tap(0.5, 0.5)


def test_tap_raises_when_iwda2_rejects_the_request(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._device_ip = DEVICE_IP
    response = MagicMock(
        status_code=500,
        text='{"status":"error","reason":"application is not running"}',
    )

    with patch(
        "idevice.device.common.iwda2.requests.get", return_value=response
    ):
        with pytest.raises(IWDA2Error, match="HTTP 500"):
            ios4_device.tap(0.5, 0.5)


def test_tap_raises_without_device_ip(ios4_device: IOSDevice4) -> None:
    with patch("idevice.device.common.iwda2.requests.get") as get:
        with pytest.raises(IWDA2Error, match="device_ip is empty"):
            ios4_device.tap(0.5, 0.5)

    get.assert_not_called()


def test_start_moniter_calls_iwda2_with_duration(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._device_ip = DEVICE_IP
    response = MagicMock(status_code=200)

    with patch(
        "idevice.device.common.iwda2.requests.get", return_value=response
    ) as get:
        assert ios4_device.start_moniter(duration=90) is True

    get.assert_called_once_with(
        f"http://{DEVICE_IP}:18201/api/monitor/start",
        params={"duration": "90"},
        timeout=30.0,
    )


def test_stop_moniter_calls_iwda2(ios4_device: IOSDevice4) -> None:
    ios4_device._device_ip = DEVICE_IP
    response = MagicMock(status_code=200)

    with patch(
        "idevice.device.common.iwda2.requests.get", return_value=response
    ) as get:
        assert ios4_device.stop_moniter() is True

    get.assert_called_once_with(
        f"http://{DEVICE_IP}:18201/api/monitor/stop",
        params=None,
        timeout=30.0,
    )


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan"), True])
def test_start_moniter_rejects_invalid_duration(
    ios4_device: IOSDevice4, duration: float
) -> None:
    with pytest.raises(ValueError, match="positive finite number"):
        ios4_device.start_moniter(duration)


def test_moniter_returns_false_when_iwda2_is_unreachable(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._device_ip = DEVICE_IP
    with patch(
        "idevice.device.common.iwda2.requests.get",
        side_effect=requests.ConnectionError("refused"),
    ):
        assert ios4_device.start_moniter() is False


def test_moniter_returns_false_without_device_ip(
    ios4_device: IOSDevice4,
) -> None:
    with patch("idevice.device.common.iwda2.requests.get") as get:
        assert ios4_device.stop_moniter() is False

    get.assert_not_called()


def test_host_is_running_is_always_false(ios4_device: IOSDevice4) -> None:
    assert ios4_device.host_is_running() is False
    ios4_device._runner.run.assert_not_called()


def test_argument_and_environment_validation() -> None:
    assert IOSDevice4._encode_launch_arguments(["foo,bar", r"C:\tmp"]) == (
        r"foo\,bar,C:\\tmp"
    )
    with pytest.raises(ValueError, match="cannot be empty"):
        IOSDevice4._encode_launch_arguments([""])
    with pytest.raises(ValueError, match="cannot contain ','"):
        IOSDevice4._encode_environment({"KEY": "a,b"})


@pytest.mark.parametrize(
    "stdout",
    [
        f'"UniqueDeviceID": String("{UDID}")\n',
        f'    "UniqueDeviceID": String(\n        "{UDID}",\n    ),\n',
    ],
    ids=["without-trailing-comma", "with-trailing-comma"],
)
def test_default_udid_parses_ideviceinfo(stdout: str) -> None:
    runner = MagicMock()
    runner.run.return_value = result(stdout=stdout)
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


def afc_command(*arguments: str) -> list[str]:
    """Build the ``afc --documents`` command the device is expected to run."""
    return [BINARY, "--udid", UDID, "afc", "--documents", APP_ID, *arguments]


def file_info(ifmt: str) -> CommandResult:
    """Fake ``afc info`` output for a directory or regular file."""
    return result(stdout=f"FileInfo {{\n    size: 14,\n    st_ifmt: {ifmt!r},\n}}\n".replace("'", '"'))


MISSING = result(returncode=134, stderr="Failed to get file info: Afc(ObjectNotFound)\n")


def listing(*names: str) -> CommandResult:
    """Fake ``afc list`` output, including the ``.``/``..`` entries."""
    entries = "".join(f'    "{name}",\n' for name in (".", "..", *names))
    return result(stdout=f"/Documents\n[\n{entries}]\n")


def route(responses: dict[str, CommandResult]) -> object:
    """Dispatch a mocked runner call on ``<subcommand> <first argument>``."""

    def _run(command: list[str], **_: object) -> CommandResult:
        key = " ".join(command[6:8])
        if key not in responses:
            raise AssertionError(f"unexpected command: {command}")
        return responses[key]

    return _run


def test_documents_path_anchors_under_documents_and_rejects_parent() -> None:
    assert IOSDevice4._documents_path("Logs/app.log") == "/Documents/Logs/app.log"
    assert IOSDevice4._documents_path("/Logs/app.log") == "/Documents/Logs/app.log"
    assert IOSDevice4._documents_path("\\Logs\\app.log") == "/Documents/Logs/app.log"
    assert IOSDevice4._documents_path("/") == "/Documents"
    with pytest.raises(ValueError, match=r"\.\."):
        IOSDevice4._documents_path("../escape")


def test_documents_helpers_validate_arguments(ios4_device: IOSDevice4) -> None:
    with pytest.raises(ValueError, match="app_id is required"):
        ios4_device.documents_exists("", "Logs")
    with pytest.raises(ValueError, match="remote is required"):
        ios4_device.documents_ls(APP_ID, "")


def test_documents_exists_uses_afc_info(ios4_device: IOSDevice4) -> None:
    ios4_device._runner.run.return_value = file_info("S_IFREG")

    assert ios4_device.documents_exists(APP_ID, "Logs/app.log") is True

    ios4_device._runner.run.assert_called_once_with(
        afc_command("info", "/Documents/Logs/app.log"), check=False
    )


def test_documents_exists_false_when_info_fails(ios4_device: IOSDevice4) -> None:
    ios4_device._runner.run.return_value = MISSING

    assert ios4_device.documents_exists(APP_ID, "Logs/app.log") is False


def test_documents_ls_lists_directory_entries(ios4_device: IOSDevice4) -> None:
    ios4_device._runner.run.side_effect = route(
        {
            "info /Documents/Logs": file_info("S_IFDIR"),
            "list /Documents/Logs": listing("app.log", "旧日志"),
        }
    )

    assert ios4_device.documents_ls(APP_ID, "Logs") == ["app.log", "旧日志"]


def test_documents_ls_on_a_file_returns_its_name(ios4_device: IOSDevice4) -> None:
    ios4_device._runner.run.return_value = file_info("S_IFREG")

    assert ios4_device.documents_ls(APP_ID, "Logs/app.log") == ["app.log"]


def test_documents_ls_raises_when_missing(ios4_device: IOSDevice4) -> None:
    ios4_device._runner.run.return_value = MISSING

    with pytest.raises(FileNotFoundError, match="/Documents/Logs"):
        ios4_device.documents_ls(APP_ID, "Logs")


def test_documents_pull_downloads_a_file(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    dest = tmp_path / "out" / "app.log"
    ios4_device._runner.run.side_effect = route(
        {
            "info /Documents/Logs/app.log": file_info("S_IFREG"),
            "download /Documents/Logs/app.log": result(),
        }
    )

    assert ios4_device.documents_pull(APP_ID, "Logs/app.log", dest) is True

    ios4_device._runner.run.assert_called_with(
        afc_command("download", "/Documents/Logs/app.log", str(dest)), check=False
    )
    assert dest.parent.is_dir()


def test_documents_pull_into_existing_dir_keeps_remote_name(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    ios4_device._runner.run.side_effect = route(
        {
            "info /Documents/Logs/app.log": file_info("S_IFREG"),
            "download /Documents/Logs/app.log": result(),
        }
    )

    assert ios4_device.documents_pull(APP_ID, "Logs/app.log", tmp_path) is True

    ios4_device._runner.run.assert_called_with(
        afc_command(
            "download", "/Documents/Logs/app.log", str(tmp_path / "app.log")
        ),
        check=False,
    )


def test_documents_pull_walks_directories(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    dest = tmp_path / "Logs"
    ios4_device._runner.run.side_effect = route(
        {
            "info /Documents/Logs": file_info("S_IFDIR"),
            "list /Documents/Logs": listing("app.log", "old"),
            "info /Documents/Logs/app.log": file_info("S_IFREG"),
            "download /Documents/Logs/app.log": result(),
            "info /Documents/Logs/old": file_info("S_IFDIR"),
            "list /Documents/Logs/old": listing("2024.log"),
            "info /Documents/Logs/old/2024.log": file_info("S_IFREG"),
            "download /Documents/Logs/old/2024.log": result(),
        }
    )

    assert ios4_device.documents_pull(APP_ID, "Logs", dest) is True

    assert (dest / "old").is_dir()
    downloads = [
        call.args[0][7]
        for call in ios4_device._runner.run.call_args_list
        if call.args[0][6] == "download"
    ]
    assert downloads == [
        "/Documents/Logs/app.log",
        "/Documents/Logs/old/2024.log",
    ]


def test_documents_pull_false_when_remote_missing(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    ios4_device._runner.run.return_value = MISSING

    assert ios4_device.documents_pull(APP_ID, "Logs", tmp_path / "out") is False


def test_documents_push_creates_remote_parent_then_uploads(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    local = tmp_path / "app.log"
    local.write_text("log")
    ios4_device._runner.run.side_effect = route(
        {
            "info /Documents/Logs/app.log": MISSING,
            "mkdir /Documents/Logs": result(),
            "upload " + str(local): result(),
        }
    )

    assert ios4_device.documents_push(APP_ID, local, "Logs/app.log") is True

    ios4_device._runner.run.assert_called_with(
        afc_command("upload", str(local), "/Documents/Logs/app.log"), check=False
    )


def test_documents_push_into_existing_dir_keeps_local_name(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    local = tmp_path / "app.log"
    local.write_text("log")
    ios4_device._runner.run.side_effect = route(
        {
            "info /Documents/Logs": file_info("S_IFDIR"),
            "mkdir /Documents/Logs": result(),
            "upload " + str(local): result(),
        }
    )

    assert ios4_device.documents_push(APP_ID, local, "Logs") is True

    ios4_device._runner.run.assert_called_with(
        afc_command("upload", str(local), "/Documents/Logs/app.log"), check=False
    )


def test_documents_push_walks_directories(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    local = tmp_path / "Logs"
    (local / "old").mkdir(parents=True)
    (local / "app.log").write_text("log")
    (local / "old" / "2024.log").write_text("old")
    ios4_device._runner.run.side_effect = route(
        {
            "info /Documents/Logs": MISSING,
            "mkdir /Documents/Logs": result(),
            "mkdir /Documents/Logs/old": result(),
            "upload " + str(local / "app.log"): result(),
            "upload " + str(local / "old" / "2024.log"): result(),
        }
    )

    assert ios4_device.documents_push(APP_ID, local, "Logs") is True

    uploads = [
        call.args[0][8]
        for call in ios4_device._runner.run.call_args_list
        if call.args[0][6] == "upload"
    ]
    assert uploads == [
        "/Documents/Logs/app.log",
        "/Documents/Logs/old/2024.log",
    ]


def test_documents_push_false_when_local_missing(
    ios4_device: IOSDevice4, tmp_path: Path
) -> None:
    assert ios4_device.documents_push(APP_ID, tmp_path / "nope", "Logs") is False
    ios4_device._runner.run.assert_not_called()


def test_documents_rm_removes_files_and_trees(ios4_device: IOSDevice4) -> None:
    ios4_device._runner.run.side_effect = route(
        {
            "info /Documents/Logs/app.log": file_info("S_IFREG"),
            "remove /Documents/Logs/app.log": result(),
        }
    )
    assert ios4_device.documents_rm(APP_ID, "Logs/app.log") is True
    ios4_device._runner.run.assert_called_with(
        afc_command("remove", "/Documents/Logs/app.log"), check=False
    )

    ios4_device._runner.run.side_effect = route(
        {
            "info /Documents/Logs": file_info("S_IFDIR"),
            "remove_all /Documents/Logs": result(),
        }
    )
    assert ios4_device.documents_rm(APP_ID, "Logs") is True
    ios4_device._runner.run.assert_called_with(
        afc_command("remove_all", "/Documents/Logs"), check=False
    )


def test_documents_rm_false_when_missing(ios4_device: IOSDevice4) -> None:
    ios4_device._runner.run.return_value = MISSING

    assert ios4_device.documents_rm(APP_ID, "Logs") is False
