"""Unit tests for the ios4-backed ``IOSDevice4`` lifecycle."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from idevice.device.base.errors import AppNotInstalledError, DeviceNotFoundError
from idevice.device.base.runner import CommandResult
from idevice.device.ios4.device import IOSDevice4, IOSDevice4Error

APP_ID = "com.example.game"
IWDA2_RUNNER_ID = "com.idevice.iwda2.xctrunner"
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


def test_run_iwda2_starts_background_xctest_client(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.return_value = result(
        stdout=f"  {IWDA2_RUNNER_ID}  iwda2-Runner  1.0\n"
    )
    process = MagicMock()
    process.pid = 7312
    process.poll.return_value = None

    with patch(
        "idevice.device.ios4.device.subprocess.Popen", return_value=process
    ) as popen:
        with patch("idevice.device.ios4.device.time.sleep"):
            startup_thread = ios4_device.run_iwda2(
                target_bundle_id=APP_ID,
                server_port=19001,
                dialog_scan_interval=0.25,
                max_session_seconds=1800,
                command_timeout_seconds=15,
            )
            startup_thread.join(timeout=1)

    assert startup_thread.is_alive() is False
    assert ios4_device.iwda2_process_id == 7312
    popen.assert_called_once_with(
        [
            BINARY,
            "--udid",
            UDID,
            "xctest",
            "--env",
            "SERVER_PORT=19001,AUTO_DISMISS_DIALOGS=true,"
            "DIALOG_SCAN_INTERVAL=0.25,MAX_SESSION_SECONDS=1800,"
            f"COMMAND_TIMEOUT_SECONDS=15,TARGET_BUNDLE_ID={APP_ID}",
            IWDA2_RUNNER_ID,
            APP_ID,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_run_iwda2_rejects_missing_runner(ios4_device: IOSDevice4) -> None:
    ios4_device._runner.run.return_value = result(stdout="Found 0 applications:\n")

    with patch("idevice.device.ios4.device.subprocess.Popen") as popen:
        with pytest.raises(AppNotInstalledError, match="iwda2 Runner not installed"):
            ios4_device.run_iwda2()

    popen.assert_not_called()


def test_run_iwda2_does_not_block_while_process_starts(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.return_value = result(
        stdout=f"  {IWDA2_RUNNER_ID}  iwda2-Runner  1.0\n"
    )
    allow_process_start = threading.Event()
    process = MagicMock()
    process.pid = 7315
    process.poll.return_value = None

    def delayed_popen(*_args: object, **_kwargs: object) -> MagicMock:
        allow_process_start.wait(timeout=1)
        return process

    with patch(
        "idevice.device.ios4.device.subprocess.Popen",
        side_effect=delayed_popen,
    ):
        with patch("idevice.device.ios4.device.time.sleep"):
            startup_thread = ios4_device.run_iwda2()
            assert startup_thread.is_alive() is True
            assert ios4_device.iwda2_process_id is None
            allow_process_start.set()
            startup_thread.join(timeout=1)

    assert startup_thread.is_alive() is False
    assert ios4_device.iwda2_process_id == 7315


def test_run_iwda2_waits_for_http_health(tmp_path: Path) -> None:
    with patch("idevice.device.ios4.device.ios4_binary", return_value=BINARY):
        with patch(
            "idevice.device.ios4.device.shutil.which", return_value=BINARY
        ):
            device = IOSDevice4(
                UDID,
                device_ip="192.0.2.10",
                cache_dir=tmp_path / "cache",
            )
    device._runner = MagicMock()
    device._runner.run.return_value = result(
        stdout=f"  {IWDA2_RUNNER_ID}  iwda2-Runner  1.0\n"
    )
    process = MagicMock()
    process.pid = 7313
    process.poll.return_value = None
    response = MagicMock()
    response.status = 200
    response.__enter__.return_value = response

    with patch(
        "idevice.device.ios4.device.subprocess.Popen", return_value=process
    ):
        with patch(
            "idevice.device.ios4.device.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            startup_thread = device.run_iwda2(server_port=19002)
            startup_thread.join(timeout=1)

    assert startup_thread.is_alive() is False
    assert device.iwda2_process_id == 7313
    urlopen.assert_called_once_with(
        "http://192.0.2.10:19002/api/health", timeout=1
    )


def test_stop_iwda2_requests_exit_on_configured_port(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._device_ip = "192.0.2.10"
    ios4_device._runner.run.return_value = result(stdout="")
    process = MagicMock()
    process.poll.side_effect = [None, None, 0]
    ios4_device._iwda2_process = process
    ios4_device._iwda2_server_port = 19003
    response = MagicMock()
    response.__enter__.return_value = response

    with patch(
        "idevice.device.ios4.device.urllib.request.urlopen",
        return_value=response,
    ) as urlopen:
        ios4_device.stop_iwda2(timeout=5)

    urlopen.assert_called_once_with(
        "http://192.0.2.10:19003/api/exit", timeout=3
    )
    process.wait.assert_called_once_with(timeout=5)
    process.terminate.assert_not_called()
    assert ios4_device.iwda2_process_id is None


def test_stop_iwda2_kills_orphaned_device_runner(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.side_effect = [
        result(stdout="  4946  iwda2-Runner  true\n"),
        result(),
    ]

    ios4_device.stop_iwda2(graceful=False)

    assert ios4_device._runner.run.call_args_list[0].args[0] == [
        BINARY,
        "--udid",
        UDID,
        "device_info",
        "processes",
    ]
    assert ios4_device._runner.run.call_args_list[1].args[0] == [
        BINARY,
        "--udid",
        UDID,
        "app_service",
        "signal",
        "4946",
        "9",
    ]


def test_run_iwda2_rejects_duplicate_client(ios4_device: IOSDevice4) -> None:
    process = MagicMock()
    process.pid = 7314
    process.poll.return_value = None
    ios4_device._iwda2_process = process

    with pytest.raises(IOSDevice4Error, match="already running"):
        ios4_device.run_iwda2()


def test_host_is_running_recognizes_iwda2_runner(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.return_value = result(
        stdout="  4946  iwda2-Runner  true\n"
    )

    assert ios4_device.host_is_running() is True


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
