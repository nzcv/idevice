"""Unit tests for the devicectl-backed ``IOSDevice5`` lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from idevice.device.base.device import AppDataPath, DeviceBase
from idevice.device.base.errors import (
    AppNotInstalledError,
    CommandExecutionError,
    DeviceNotFoundError,
)
from idevice.device.base.runner import CommandResult
from idevice.device.common.ios4cli import IOS4CLI
from idevice.device.common.xcruncli import DevicectlOutcome, XcrunCLI
from idevice.device.ios5.device import IOSDevice5, IOSDevice5Error

APP_ID = "com.example.game"
IOS4_BINARY = "/opt/ios4"
UDID = "00000000-0000000000000000"
DEVICE_IP = "192.0.2.20"
APP_URL = "file:///private/var/containers/Bundle/Application/AAAA/ExampleGame.app/"


@pytest.fixture
def ios5_device(tmp_path: Path) -> IOSDevice5:
    """Build an IOSDevice5 with a mocked runner and isolated cache."""
    with patch("idevice.device.ios5.device.sys.platform", "darwin"):
        with patch("idevice.device.common.xcruncli.shutil.which", return_value="/usr/bin/xcrun"):
            with patch(
                "idevice.device.ios5.device.ios4_binary",
                return_value=IOS4_BINARY,
            ):
                device = IOSDevice5(
                    UDID,
                    device_ip=DEVICE_IP,
                    package_name=APP_ID,
                    cache_dir=tmp_path / "cache",
                )
    runner = MagicMock()
    device._xcruncli.runner = runner
    device._ios4cli.runner = runner
    return device


def outcome(
    result: dict[str, Any] | None = None,
    *,
    returncode: int = 0,
    error: str = "",
) -> DevicectlOutcome:
    """Create a devicectl outcome for mocked ``_run`` calls."""
    return DevicectlOutcome(
        returncode=returncode, result=result or {}, error=error
    )


def app_listing(bundle_id: str, url: str) -> DevicectlOutcome:
    """Create a ``device info apps`` outcome holding one application."""
    return outcome({"apps": [{"bundleIdentifier": bundle_id, "url": url}]})


def command_of(mock: MagicMock, index: int) -> list[str]:
    """Return the devicectl arguments of the ``index``-th ``_run`` call."""
    return mock.call_args_list[index].args[0]


def test_construction_requires_macos(tmp_path: Path) -> None:
    with patch("idevice.device.ios5.device.sys.platform", "win32"):
        with pytest.raises(IOSDevice5Error, match="only available on macOS"):
            IOSDevice5(UDID, cache_dir=tmp_path)


def test_construction_requires_xcrun(tmp_path: Path) -> None:
    with patch("idevice.device.ios5.device.sys.platform", "darwin"):
        with patch("idevice.device.common.xcruncli.shutil.which", return_value=None):
            with pytest.raises(IOSDevice5Error, match="CLI not found"):
                IOSDevice5(UDID, cache_dir=tmp_path)


def test_ios5_uses_the_common_ios4cli_type(ios5_device: IOSDevice5) -> None:
    assert isinstance(ios5_device._ios4cli, IOS4CLI)
    assert ios5_device._ios4cli.runner is ios5_device._xcruncli.runner


def test_ios5_composes_xcruncli_instead_of_inheriting_it(
    ios5_device: IOSDevice5,
) -> None:
    assert isinstance(ios5_device, DeviceBase)
    assert not isinstance(ios5_device, XcrunCLI)
    assert isinstance(ios5_device._xcruncli, XcrunCLI)
    assert ios5_device._xcruncli.runner is ios5_device._ios4cli.runner
    assert not hasattr(ios5_device._xcruncli, "app_cache")
    assert ios5_device._app_cache is not None


def test_run_parses_json_document_and_removes_temporary_file(
    ios5_device: IOSDevice5,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> CommandResult:
        json_path = Path(command[command.index("--json-output") + 1])
        json_path.write_text(
            json.dumps({"info": {}, "result": {"apps": []}}), encoding="utf-8"
        )
        seen["command"] = command
        seen["json_path"] = json_path
        seen["kwargs"] = kwargs
        return CommandResult(returncode=0, stdout="", stderr="")

    ios5_device._xcruncli.runner.run = fake_run
    parsed = ios5_device._xcruncli.run(["device", "info", "apps"], timeout=45)

    assert parsed.succeeded is True
    assert parsed.result == {"apps": []}
    assert seen["command"][:2] == ["xcrun", "devicectl"]
    assert seen["command"][2:5] == ["device", "info", "apps"]
    assert "--quiet" in seen["command"]
    assert seen["command"][seen["command"].index("--timeout") + 1] == "45"
    assert seen["kwargs"] == {"check": False, "timeout": 75}
    assert not seen["json_path"].exists()


def test_run_places_output_options_before_argument_separator(
    ios5_device: IOSDevice5,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> CommandResult:
        json_path = Path(command[command.index("--json-output") + 1])
        json_path.write_text(
            json.dumps({"info": {}, "result": {"process": {"processIdentifier": 42}}}),
            encoding="utf-8",
        )
        seen["command"] = command
        return CommandResult(returncode=0, stdout="", stderr="")

    ios5_device._xcruncli.runner.run = fake_run
    parsed = ios5_device._xcruncli.run(
        [
            "device",
            "process",
            "launch",
            "--device",
            UDID,
            "--",
            APP_ID,
            "--mode",
            "debug",
        ]
    )

    command = seen["command"]
    separator_index = command.index("--")
    assert command.index("--quiet") < separator_index
    assert command.index("--timeout") < separator_index
    assert command.index("--json-output") < separator_index
    assert command[separator_index + 1 :] == [APP_ID, "--mode", "debug"]
    assert parsed.result == {"process": {"processIdentifier": 42}}


def test_run_surfaces_nested_coredevice_error(ios5_device: IOSDevice5) -> None:
    document = {
        "error": {
            "code": 4000,
            "domain": "com.apple.dt.CoreDeviceError",
            "userInfo": {
                "NSLocalizedDescription": {"string": "The tunnel was interrupted."},
                "NSUnderlyingError": {
                    "error": {
                        "code": 60,
                        "domain": "Network.NWError",
                        "userInfo": {
                            "NSLocalizedDescription": {"string": "Operation timed out"}
                        },
                    }
                },
            },
        }
    }

    def fake_run(command: list[str], **kwargs: Any) -> CommandResult:
        Path(command[command.index("--json-output") + 1]).write_text(
            json.dumps(document), encoding="utf-8"
        )
        return CommandResult(returncode=1, stdout="", stderr="")

    ios5_device._xcruncli.runner.run = fake_run
    parsed = ios5_device._xcruncli.run(["device", "info", "apps"])

    assert parsed.succeeded is False
    assert parsed.error == "The tunnel was interrupted.: Operation timed out"


def test_default_udid_returns_the_first_usb_device() -> None:
    devices = {
        "devices": [
            {
                "hardwareProperties": {"udid": "over-wifi"},
                "connectionProperties": {
                    "tunnelState": "connected",
                    "transportType": "localNetwork",
                },
            },
            {
                "hardwareProperties": {"udid": UDID},
                "connectionProperties": {
                    "tunnelState": "disconnected",
                    "transportType": "wired",
                },
            },
            {
                "hardwareProperties": {"udid": "second-cable"},
                "connectionProperties": {
                    "tunnelState": "connected",
                    "transportType": "wired",
                },
            },
        ]
    }
    with patch(
        "idevice.device.common.xcruncli._run_devicectl",
        return_value=outcome(devices),
    ):
        assert IOSDevice5.default_udid() == UDID


def test_default_udid_rejects_devices_that_are_not_cabled() -> None:
    devices = {
        "devices": [
            {
                "hardwareProperties": {"udid": "forgotten"},
                "connectionProperties": {"tunnelState": "unavailable"},
            },
            {
                "hardwareProperties": {"udid": "over-wifi"},
                "connectionProperties": {
                    "tunnelState": "connected",
                    "transportType": "localNetwork",
                },
            },
        ]
    }
    with patch(
        "idevice.device.common.xcruncli._run_devicectl",
        return_value=outcome(devices),
    ):
        with pytest.raises(DeviceNotFoundError, match="No USB-attached device"):
            IOSDevice5.default_udid()


def test_default_udid_rejects_an_empty_device_list() -> None:
    with patch(
        "idevice.device.common.xcruncli._run_devicectl",
        return_value=outcome({}),
    ):
        with pytest.raises(DeviceNotFoundError):
            IOSDevice5.default_udid()


def test_install_caches_the_bundle_id_reported_by_devicectl(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    ipa = tmp_path / "ExampleGame.ipa"
    ipa.write_bytes(b"ipa")
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome(
            {
                "installedApplications": [
                    {"bundleID": APP_ID, "installationURL": APP_URL}
                ]
            }
        )
    )

    assert ios5_device.install(ipa) is True

    assert command_of(ios5_device._xcruncli.run, 0) == [
        "device",
        "install",
        "app",
        "--device",
        UDID,
        str(ipa),
    ]
    cached = ios5_device._app_cache.get(APP_ID)
    assert cached is not None
    assert cached.path == APP_URL


def test_install_returns_false_on_a_devicectl_error(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    ipa = tmp_path / "ExampleGame.ipa"
    ipa.write_bytes(b"ipa")
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome(returncode=1, error="The device was not found.")
    )
    ios4cli = MagicMock()
    ios4cli.install.return_value = False
    ios5_device._ios4cli = ios4cli

    assert ios5_device.install(ipa, app_id=APP_ID) is False
    assert ios5_device._app_cache.get(APP_ID) is None


def test_install_prefers_the_explicit_app_id(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    ipa = tmp_path / "ExampleGame.ipa"
    ipa.write_bytes(b"ipa")
    reported_app_id = f"{APP_ID}.reported"
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome(
            {
                "installedApplications": [
                    {
                        "bundleID": reported_app_id,
                        "installationURL": APP_URL,
                    }
                ]
            }
        )
    )

    assert ios5_device.install(ipa, app_id=APP_ID) is True
    assert ios5_device._app_cache.get(APP_ID) is not None
    assert ios5_device._app_cache.get(reported_app_id) is None


def test_install_falls_back_to_ios4_after_devicectl_failure(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    ipa = tmp_path / "ExampleGame.ipa"
    ipa.write_bytes(b"ipa")
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome(returncode=1, error="The tunnel was interrupted.")
    )
    ios4cli = MagicMock()
    ios4cli.install.return_value = True
    ios5_device._ios4cli = ios4cli

    assert ios5_device.install(ipa, app_id=APP_ID) is True

    ios4cli.install.assert_called_once_with(ipa)
    assert ios5_device._app_cache.get(APP_ID) is not None


def test_is_installed_falls_back_only_when_devicectl_fails(
    ios5_device: IOSDevice5,
) -> None:
    ios4cli = MagicMock()
    ios4cli.is_installed.return_value = True
    ios5_device._ios4cli = ios4cli
    ios5_device._xcruncli.run = MagicMock(return_value=outcome({"apps": []}))

    assert ios5_device.is_installed(APP_ID) is False
    ios4cli.is_installed.assert_not_called()

    ios5_device._xcruncli.run.return_value = outcome(returncode=1, error="device offline")
    assert ios5_device.is_installed(APP_ID) is True
    ios4cli.is_installed.assert_called_once_with(APP_ID)


def test_install_rejects_missing_package(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError, match="Package not found"):
        ios5_device.install(tmp_path / "missing.ipa", app_id=APP_ID)


def test_is_installed_matches_only_an_exact_bundle_id(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.run = MagicMock(
        return_value=app_listing(f"{APP_ID}.beta", APP_URL)
    )

    assert ios5_device.is_installed(APP_ID) is False
    assert command_of(ios5_device._xcruncli.run, 0)[:5] == [
        "device",
        "info",
        "apps",
        "--device",
        UDID,
    ]
    assert "--bundle-id" in command_of(ios5_device._xcruncli.run, 0)


def test_launch_passes_environment_and_ordered_arguments(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.run = MagicMock(
        side_effect=[
            app_listing(APP_ID, APP_URL),
            outcome({"process": {"processIdentifier": 4815}}),
        ]
    )

    ios5_device.launch_app(
        APP_ID,
        args=["--mode", "debug", "--label", "foo,bar"],
        environment={"MallocStackLogging": "1", "FOO": "bar=baz"},
    )

    assert ios5_device.last_launch_pid == 4815
    launch = command_of(ios5_device._xcruncli.run, 1)
    assert launch[:5] == ["device", "process", "launch", "--device", UDID]
    assert json.loads(launch[launch.index("--environment-variables") + 1]) == {
        "FOO": "bar=baz",
        "MallocStackLogging": "1",
    }
    assert launch[launch.index("--") + 1 :] == [
        APP_ID,
        "--mode",
        "debug",
        "--label",
        "foo,bar",
    ]


def test_launch_app_normalizes_an_empty_environment(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.launch_app = MagicMock()

    ios5_device.launch_app(APP_ID, environment={})

    ios5_device._xcruncli.launch_app.assert_called_once_with(
        APP_ID,
        args=None,
        environment=None,
        terminate_existing=True,
        activate=True,
    )


def test_launch_skips_install_check_for_an_explicit_app_id(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome({"process": {"processIdentifier": 4815}})
    )

    ios5_device.launch(APP_ID)

    assert ios5_device._xcruncli.run.call_count == 1
    assert command_of(ios5_device._xcruncli.run, 0)[:3] == [
        "device",
        "process",
        "launch",
    ]
    assert ios5_device.last_launch_pid is None


def test_launch_tracks_the_default_app_pid(ios5_device: IOSDevice5) -> None:
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome({"process": {"processIdentifier": 4815}})
    )

    ios5_device.launch()

    assert ios5_device.last_launch_pid == 4815


def test_launch_app_falls_back_to_ios4(ios5_device: IOSDevice5) -> None:
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome(returncode=1, error="device offline")
    )
    ios4cli = MagicMock()
    ios4cli.last_launch_pid = 912
    ios4cli.last_launch_app_id = APP_ID
    ios5_device._ios4cli = ios4cli

    ios5_device.launch_app(APP_ID, args=["--debug"], environment={"MODE": "test"})

    ios4cli.launch_app.assert_called_once_with(
        APP_ID, args=["--debug"], environment={"MODE": "test"}
    )
    assert ios5_device.last_launch_pid == 912


def test_launch_app_fallback_normalizes_an_empty_environment(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.launch_app = MagicMock(
        side_effect=IOSDevice5Error("device offline")
    )
    ios4cli = MagicMock()
    ios4cli.last_launch_pid = 912
    ios4cli.last_launch_app_id = APP_ID
    ios5_device._ios4cli = ios4cli

    ios5_device.launch_app(APP_ID, environment={})

    ios4cli.launch_app.assert_called_once_with(
        APP_ID, args=None, environment=None
    )


def test_launch_rejects_an_app_that_is_not_installed(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.run = MagicMock(return_value=outcome({"apps": []}))

    with pytest.raises(AppNotInstalledError):
        ios5_device.launch_app(APP_ID)


def test_xcrun_launch_reports_an_unreachable_device_as_such(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome(returncode=1, error="The tunnel was interrupted.")
    )

    with pytest.raises(IOSDevice5Error, match="App listing failed"):
        ios5_device._xcruncli.launch_app(APP_ID)


def test_xcrun_launch_requires_a_pid_in_the_result(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.run = MagicMock(
        side_effect=[app_listing(APP_ID, APP_URL), outcome({"process": {}})]
    )

    with pytest.raises(IOSDevice5Error, match="returned no PID"):
        ios5_device._xcruncli.launch_app(APP_ID)


def test_stop_app_terminates_only_processes_of_that_bundle(
    ios5_device: IOSDevice5,
) -> None:
    processes = {
        "runningProcesses": [
            {"executable": f"{APP_URL}ExampleGame", "processIdentifier": 501},
            {
                "executable": "file:///Applications/Other.app/Other",
                "processIdentifier": 502,
            },
            {"executable": "file:///sbin/launchd", "processIdentifier": 1},
        ]
    }
    ios5_device._xcruncli.run = MagicMock(
        side_effect=[
            app_listing(APP_ID, APP_URL),
            outcome(processes),
            outcome({}),
        ]
    )

    ios5_device.stop_app(APP_ID)

    terminate = command_of(ios5_device._xcruncli.run, 2)
    assert terminate == [
        "device",
        "process",
        "terminate",
        "--device",
        UDID,
        "--pid",
        "501",
        "--kill",
    ]


def test_stop_app_rejects_an_app_that_is_not_installed(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.run = MagicMock(return_value=outcome({"apps": []}))

    with pytest.raises(AppNotInstalledError):
        ios5_device.stop_app(APP_ID)


def test_host_is_running_detects_the_runner_executable(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome(
            {
                "runningProcesses": [
                    {
                        "executable": "file:///Applications/WebDriverAgentRunner",
                        "processIdentifier": 3,
                    }
                ]
            }
        )
    )

    assert ios5_device.host_is_running() is True


def test_start_moniter_calls_iwda2_with_duration(
    ios5_device: IOSDevice5,
) -> None:
    response = MagicMock(status_code=200)

    with patch(
        "idevice.device.ios5.device.requests.get", return_value=response
    ) as get:
        assert ios5_device.start_moniter(duration=90) is True

    get.assert_called_once_with(
        f"http://{DEVICE_IP}:18201/api/monitor/start",
        params={"duration": "90"},
        timeout=30.0,
    )


def test_stop_moniter_calls_iwda2(ios5_device: IOSDevice5) -> None:
    response = MagicMock(status_code=200)

    with patch(
        "idevice.device.ios5.device.requests.get", return_value=response
    ) as get:
        assert ios5_device.stop_moniter() is True

    get.assert_called_once_with(
        f"http://{DEVICE_IP}:18201/api/monitor/stop",
        params=None,
        timeout=30.0,
    )


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan"), True])
def test_start_moniter_rejects_invalid_duration(
    ios5_device: IOSDevice5, duration: float
) -> None:
    with pytest.raises(ValueError, match="positive finite number"):
        ios5_device.start_moniter(duration)


def test_moniter_returns_false_when_iwda2_is_unreachable(
    ios5_device: IOSDevice5,
) -> None:
    with patch(
        "idevice.device.ios5.device.requests.get",
        side_effect=requests.ConnectionError("refused"),
    ):
        assert ios5_device.start_moniter() is False


def test_moniter_returns_false_without_device_ip(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._device_ip = ""

    with patch("idevice.device.ios5.device.requests.get") as get:
        assert ios5_device.stop_moniter() is False

    get.assert_not_called()


def test_moniter_returns_false_for_http_error(
    ios5_device: IOSDevice5,
) -> None:
    response = MagicMock(status_code=409, text="disabled")

    with patch(
        "idevice.device.ios5.device.requests.get", return_value=response
    ):
        assert ios5_device.start_moniter() is False


def test_tap_calls_iwda2_with_normalized_coordinates_and_bundle_id(
    ios5_device: IOSDevice5,
) -> None:
    response = MagicMock(status_code=200)

    with patch(
        "idevice.device.ios5.device.requests.get", return_value=response
    ) as get:
        ios5_device.tap(0.25, 0.75, app_id="com.example.foreground")

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
    ios5_device: IOSDevice5,
) -> None:
    response = MagicMock(status_code=200)

    with patch(
        "idevice.device.ios5.device.requests.get", return_value=response
    ) as get:
        ios5_device.tap(0, 1)

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
    ios5_device: IOSDevice5,
    x: float,
    y: float,
    coordinate: str,
) -> None:
    with patch("idevice.device.ios5.device.requests.get") as get:
        with pytest.raises(
            ValueError, match=rf"{coordinate} must be a normalized coordinate"
        ):
            ios5_device.tap(x, y)

    get.assert_not_called()


def test_tap_raises_when_iwda2_is_unreachable(
    ios5_device: IOSDevice5,
) -> None:
    with patch(
        "idevice.device.ios5.device.requests.get",
        side_effect=requests.ConnectionError("refused"),
    ):
        with pytest.raises(IOSDevice5Error, match="tap request failed"):
            ios5_device.tap(0.5, 0.5)


def test_tap_raises_when_iwda2_rejects_the_request(
    ios5_device: IOSDevice5,
) -> None:
    response = MagicMock(
        status_code=500,
        text='{"status":"error","reason":"application is not running"}',
    )

    with patch(
        "idevice.device.ios5.device.requests.get", return_value=response
    ):
        with pytest.raises(IOSDevice5Error, match="HTTP 500"):
            ios5_device.tap(0.5, 0.5)


def test_tap_raises_without_device_ip(ios5_device: IOSDevice5) -> None:
    ios5_device._device_ip = ""

    with patch("idevice.device.ios5.device.requests.get") as get:
        with pytest.raises(IOSDevice5Error, match="device_ip is empty"):
            ios5_device.tap(0.5, 0.5)

    get.assert_not_called()


XCODE_26_DEVICE_HELP = """SUBCOMMANDS:
  copy                    Copy files.
  info                    Commands that provide information about a device
  install                 Install content onto a device.
"""

XCODE_27_DEVICE_HELP = """SUBCOMMANDS:
  capture                 Capture the device's screen.
  copy                    Copy files.
  info                    Commands that provide information about a device
"""


def test_screenshot_uses_ios4_without_devicectl_capture(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    def capture(command: list[str], **_kwargs: Any) -> CommandResult:
        if command[:4] == ["xcrun", "devicectl", "device", "--help"]:
            return CommandResult(
                returncode=0, stdout=XCODE_26_DEVICE_HELP, stderr=""
            )
        Path(command[-1]).write_bytes(b"ios4-png")
        return CommandResult(returncode=0, stdout="", stderr="")

    ios5_device._xcruncli.runner.run.side_effect = capture
    destination = tmp_path / "shots" / "screen.png"

    assert ios5_device.screenshot(destination) is True

    assert destination.read_bytes() == b"ios4-png"
    ios4_command = ios5_device._xcruncli.runner.run.call_args_list[1].args[0]
    assert ios4_command[:4] == [IOS4_BINARY, "--udid", UDID, "screenshot"]
    assert ios5_device._xcruncli.runner.run.call_args_list[1].kwargs == {
        "check": False,
        "timeout": 60,
    }


def test_screenshot_uses_ios4_when_devicectl_capture_fails(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    def capture(command: list[str], **_kwargs: Any) -> CommandResult:
        if command[:4] == ["xcrun", "devicectl", "device", "--help"]:
            return CommandResult(
                returncode=0, stdout=XCODE_27_DEVICE_HELP, stderr=""
            )
        Path(command[-1]).write_bytes(b"ios4-png")
        return CommandResult(returncode=0, stdout="", stderr="")

    ios5_device._xcruncli.runner.run.side_effect = capture
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome(returncode=1, error="capture unavailable")
    )
    destination = tmp_path / "screen.png"

    assert ios5_device.screenshot(destination) is True

    assert destination.read_bytes() == b"ios4-png"


def test_screenshot_fails_when_devicectl_and_ios4_fail(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    ios5_device._xcruncli.runner.run.side_effect = [
        CommandResult(returncode=0, stdout=XCODE_27_DEVICE_HELP, stderr=""),
        CommandResult(returncode=1, stdout="", stderr="ios4 failed"),
    ]
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome(returncode=1, error="devicectl failed")
    )
    destination = tmp_path / "shots" / "screen.png"

    assert ios5_device.screenshot(destination) is False

    assert destination.exists() is False


def test_screenshot_uses_devicectl_capture_when_available(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    ios5_device._xcruncli.runner.run.return_value = CommandResult(
        returncode=0, stdout=XCODE_27_DEVICE_HELP, stderr=""
    )
    destination = tmp_path / "screen.png"

    def fake_run(arguments: list[str], **kwargs: Any) -> DevicectlOutcome:
        Path(arguments[arguments.index("--destination") + 1]).write_bytes(b"png")
        return outcome({})

    ios5_device._xcruncli.run = MagicMock(side_effect=fake_run)

    assert ios5_device.screenshot(destination) is True
    assert destination.read_bytes() == b"png"
    assert command_of(ios5_device._xcruncli.run, 0)[:3] == ["device", "capture", "screenshot"]


def test_capture_memgraph_delegates_to_the_ios4_cli(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    ios5_device._xcruncli.last_launch_pid = 4815
    destination = tmp_path / "game.memgraph"

    def fake_run(command: list[str], **kwargs: Any) -> CommandResult:
        Path(command[-1]).write_bytes(b"memgraph")
        return CommandResult(returncode=0, stdout="", stderr="")

    ios5_device._xcruncli.runner.run = MagicMock(side_effect=fake_run)

    assert ios5_device.capture_memgraph(destination) == destination

    assert destination.read_bytes() == b"memgraph"
    command = ios5_device._xcruncli.runner.run.call_args.args[0]
    assert command[:5] == [IOS4_BINARY, "--udid", UDID, "memgraph", "4815"]


def test_capture_memgraph_propagates_a_missing_ios4_command(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    ios5_device._xcruncli.last_launch_pid = 4815
    ios5_device._xcruncli.runner.run.side_effect = CommandExecutionError(
        "ios4 missing"
    )

    with pytest.raises(CommandExecutionError, match="ios4 missing"):
        ios5_device.capture_memgraph(tmp_path / "game.memgraph")


def test_push_scopes_the_transfer_to_the_documents_sandbox(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    payload = tmp_path / "save.json"
    payload.write_text("{}", encoding="utf-8")
    ios5_device._xcruncli.run = MagicMock(return_value=outcome({}))

    assert ios5_device.documents_push(APP_ID, payload, "saves/save.json") is True

    command = command_of(ios5_device._xcruncli.run, 0)
    assert command[:3] == ["device", "copy", "to"]
    assert command[command.index("--domain-type") + 1] == "appDataContainer"
    assert command[command.index("--domain-identifier") + 1] == APP_ID
    assert command[command.index("--destination") + 1] == "Documents/saves/save.json"
    assert "--remove-existing-content" not in command


def test_push_can_replace_a_documents_directory(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    payload = tmp_path / "saves"
    payload.mkdir()
    (payload / "save.json").write_text("{}", encoding="utf-8")
    ios5_device._xcruncli.run = MagicMock(return_value=outcome({}))

    assert (
        ios5_device.documents_push(
            APP_ID,
            payload,
            "saves",
            remove_existing_content=True,
        )
        is True
    )

    command = command_of(ios5_device._xcruncli.run, 0)
    assert command[:3] == ["device", "copy", "to"]
    assert command[command.index("--source") + 1] == str(payload)
    assert command[command.index("--destination") + 1] == "Documents/saves"
    assert command[command.index("--remove-existing-content") + 1] == "true"


def test_pull_reads_the_container_root_for_local_app_data(
    ios5_device: IOSDevice5, tmp_path: Path
) -> None:
    ios5_device._xcruncli.run = MagicMock(return_value=outcome({}))

    assert ios5_device.pull2(AppDataPath.Local, "Library/Caches", tmp_path / "out")

    command = command_of(ios5_device._xcruncli.run, 0)
    assert command[:3] == ["device", "copy", "from"]
    assert command[command.index("--source") + 1] == "Library/Caches"


def test_documents_exists_matches_a_listed_entry(ios5_device: IOSDevice5) -> None:
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome({"files": [{"path": "Documents/saves/save.json"}]})
    )

    assert ios5_device.documents_exists(APP_ID, "saves/save.json") is True
    command = command_of(ios5_device._xcruncli.run, 0)
    assert command[command.index("--subdirectory") + 1] == "Documents/saves"
    assert "--no-recurse" in command


def test_ls_lists_the_documents_root_by_default(ios5_device: IOSDevice5) -> None:
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome(
            {"files": [{"path": "Documents/save.json"}, {"path": "Documents/logs"}]}
        )
    )

    assert ios5_device.ls("/", app_id=APP_ID) == [
        "Documents/save.json",
        "Documents/logs",
    ]

    command = command_of(ios5_device._xcruncli.run, 0)
    assert command[:3] == ["device", "info", "files"]
    assert command[command.index("--subdirectory") + 1] == "Documents"
    assert "--no-recurse" in command


def test_ls_can_list_the_full_app_container_root(ios5_device: IOSDevice5) -> None:
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome({"files": [{"path": "Documents"}, {"path": "Library"}]})
    )

    assert ios5_device.ls("/", app_id=APP_ID, documents_only=False) == [
        "Documents",
        "Library",
    ]

    command = command_of(ios5_device._xcruncli.run, 0)
    assert "--subdirectory" not in command


def test_documents_ls_lists_the_documents_root(ios5_device: IOSDevice5) -> None:
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome({"files": [{"path": "Documents/save.json"}]})
    )

    assert ios5_device.documents_ls(APP_ID, "/") == ["Documents/save.json"]

    command = command_of(ios5_device._xcruncli.run, 0)
    assert command[command.index("--subdirectory") + 1] == "Documents"
    assert "--no-recurse" in command


def test_ls_reports_a_failed_listing(ios5_device: IOSDevice5) -> None:
    ios5_device._xcruncli.run = MagicMock(
        return_value=outcome(returncode=1, error="No such file or directory")
    )

    with pytest.raises(IOSDevice5Error, match="Could not list"):
        ios5_device._xcruncli.ls("Documents", app_id=APP_ID)


def test_documents_rm_copies_ios4_directory_removal_workflow(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.runner.run.side_effect = [
        CommandResult(returncode=0, stdout='st_ifmt: "S_IFDIR"', stderr=""),
        CommandResult(returncode=0, stdout="", stderr=""),
    ]

    assert ios5_device.documents_rm(APP_ID, "saves") is True

    prefix = [
        IOS4_BINARY,
        "--udid",
        UDID,
        "afc",
        "--documents",
        APP_ID,
    ]
    assert ios5_device._xcruncli.runner.run.call_args_list == [
        call([*prefix, "info", "/Documents/saves"], check=False),
        call([*prefix, "remove_all", "/Documents/saves"], check=False),
    ]


def test_documents_rm_routes_directly_to_ios4(
    ios5_device: IOSDevice5,
) -> None:
    ios4cli = MagicMock()
    ios4cli.documents_rm.return_value = True
    ios5_device._ios4cli = ios4cli
    ios5_device._xcruncli.run = MagicMock()

    assert ios5_device.documents_rm(APP_ID, "saves") is True

    ios4cli.documents_rm.assert_called_once_with(APP_ID, "saves")
    ios5_device._xcruncli.run.assert_not_called()


def test_documents_rm_uses_ios4_remove_for_a_file(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.runner.run.side_effect = [
        CommandResult(returncode=0, stdout='st_ifmt: "S_IFREG"', stderr=""),
        CommandResult(returncode=0, stdout="", stderr=""),
    ]

    assert ios5_device.documents_rm(APP_ID, "Logs/app.log") is True

    command = ios5_device._xcruncli.runner.run.call_args_list[1].args[0]
    assert command[-2:] == ["remove", "/Documents/Logs/app.log"]


def test_documents_rm_returns_false_when_ios4_info_cannot_find_the_path(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.runner.run.return_value = CommandResult(
        returncode=-6,
        stdout="",
        stderr="Failed to get file info: Afc(ObjectNotFound)",
    )

    assert ios5_device.documents_rm(APP_ID, "saves") is False

    assert len(ios5_device._xcruncli.runner.run.call_args_list) == 1


def test_documents_rm_reports_a_failed_ios4_remove(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.runner.run.side_effect = [
        CommandResult(returncode=0, stdout='st_ifmt: "S_IFDIR"', stderr=""),
        CommandResult(returncode=1, stdout="", stderr="remove failed"),
    ]

    assert ios5_device.documents_rm(APP_ID, "saves") is False


def test_documents_rm_rejects_parent_path_segments(
    ios5_device: IOSDevice5,
) -> None:
    with pytest.raises(ValueError, match="must not contain"):
        ios5_device.documents_rm(APP_ID, "saves/../Library")

    ios5_device._xcruncli.runner.run.assert_not_called()


def test_documents_rm_reports_an_ios4_command_error(
    ios5_device: IOSDevice5,
) -> None:
    ios5_device._xcruncli.runner.run.side_effect = CommandExecutionError("afc failed")

    assert ios5_device.documents_rm(APP_ID, "saves") is False


def test_delete2_without_a_coredevice_service_is_unsupported(
    ios5_device: IOSDevice5,
) -> None:
    with pytest.raises(NotImplementedError, match="delete2"):
        ios5_device.delete2(AppDataPath.Persistent, "saves")
