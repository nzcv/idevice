"""Unit tests for the WDA-backed ``IOSDevice6`` lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from idevice.device.base.device import AppDataPath, DeviceBase
from idevice.device.base.errors import AppNotInstalledError
from idevice.device.base.runner import CommandResult
from idevice.device.common.ios4cli import IOS4CLI
from idevice.device.common.iwda2 import IWDA2Mixin
from idevice.device.common.wdacli import (
    ACCEPT_ALERT_BUTTON_LABELS,
    AUTO_CLICK_ALERT_SETTING,
    WDA_READY_TIMEOUT,
    WDACLI,
    AlertAction,
    build_accept_alert_selector,
)
from idevice.device.ios6.device import IOSDevice6, IOSDevice6Error

APP_ID = "com.example.game"
RUNNER_BUNDLE_ID = "com.example.WebDriverAgentRunner.xctrunner"
BINARY = "/opt/ios4"
IDEVICEINSTALLER = "/opt/bin/ideviceinstaller"
UDID = "00000000-0000000000000000"
DEVICE_IP = "192.0.2.60"
INSTALLED_LISTING = f"{APP_ID}        ExampleGame       1.0\n"


class FakeElement:
    """Stand-in for a ``facebook-wda`` element query result."""

    def __init__(self, *, exists: bool) -> None:
        self.exists = exists


class FakeAlert:
    """Stand-in for ``wda.Client.alert``."""

    def __init__(self) -> None:
        self.exists = False


class FakeWDAClient:
    """Record the WebDriverAgent calls ``WDACLI`` makes."""

    def __init__(self, url: str | None = None) -> None:
        self.url = url
        self.session_error: Exception | None = None
        self.click_error: Exception | None = None
        self.ready = True
        self.settings_error: Exception | None = None
        self.launch_pid: object = 4242
        self.sessions: list[tuple[str, Any, Any, Any]] = []
        self.terminated: list[str] = []
        self.clicks: list[tuple[float, float]] = []
        self.queries: list[tuple[str, float]] = []
        self.alert = FakeAlert()
        self.ready_waits: list[float] = []
        self.settings: list[dict[str, Any]] = []
        # Ordered names of the calls that reach WDA, so a test can assert that
        # settings land before the session the alerts monitor belongs to.
        self.calls: list[str] = []

    def appium_settings(self, value: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append("appium_settings")
        if self.settings_error is not None:
            raise self.settings_error
        if value is None:
            return self.settings[-1] if self.settings else {}
        self.settings.append(value)
        return value

    def is_ready(self) -> bool:
        return self.ready

    def wait_ready(self, timeout: float = 0.0) -> bool:
        self.ready_waits.append(timeout)
        return self.ready

    def session(
        self,
        bundle_id: str,
        arguments: list[str] | None = None,
        environment: dict[str, str] | None = None,
        alert_action: str | None = None,
    ) -> FakeWDAClient:
        self.calls.append("session")
        if self.session_error is not None:
            raise self.session_error
        self.sessions.append((bundle_id, arguments, environment, alert_action))
        return self

    def app_list(self) -> list[dict[str, Any]]:
        self.calls.append("app_list")
        raise AssertionError("launch PID must come from the session response")

    @property
    def pid(self) -> object:
        return self.launch_pid

    def app_terminate(self, bundle_id: str) -> None:
        self.terminated.append(bundle_id)

    def click(self, x: float, y: float) -> None:
        if self.click_error is not None:
            raise self.click_error
        self.clicks.append((x, y))

    def __call__(self, text: str = "", timeout: float = 0.0) -> FakeElement:
        self.queries.append((text, timeout))
        return FakeElement(exists=False)


@pytest.fixture
def wda_client() -> FakeWDAClient:
    """Return the fake WebDriverAgent client bound to the device."""
    return FakeWDAClient()


@pytest.fixture
def ios6_device(wda_client: FakeWDAClient, tmp_path: Path) -> IOSDevice6:
    """Build an IOSDevice6 with a mocked runner, fake WDA and isolated cache."""
    with patch("idevice.device.ios6.device.ios4_binary", return_value=BINARY):
        with patch(
            "idevice.device.common.ios4cli.shutil.which", return_value=BINARY
        ):
            device = IOSDevice6(
                UDID,
                device_ip=DEVICE_IP,
                package_name=APP_ID,
                cache_dir=tmp_path / "cache",
            )
    device._ios4cli.runner = MagicMock()
    device._wda.client_factory = lambda url: wda_client
    return device


def result(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> CommandResult:
    """Create a subprocess result for mocked runner calls."""
    return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)


def ios4_commands(device: IOSDevice6) -> list[list[str]]:
    """Return every ios4 argv the mocked runner received."""
    return [call.args[0] for call in device._ios4cli.runner.run.call_args_list]


def label_count(selector: str) -> int:
    """Return how many quoted labels a class chain selector matches."""
    return selector.count("'") // 2


def test_ios6_composes_the_shared_cli_layers(ios6_device: IOSDevice6) -> None:
    assert isinstance(ios6_device._ios4cli, IOS4CLI)
    assert isinstance(ios6_device._wda, WDACLI)
    assert ios6_device.platform == "ios6"
    assert ios6_device.wda_url() == f"http://{DEVICE_IP}:8100"


def test_ios6_taps_through_wda_and_has_no_iwda2_monitors() -> None:
    assert IOSDevice6.tap is not IWDA2Mixin.tap
    # The backend does not mix in the iwda2 monitors: the DeviceBase defaults
    # stand, so monitoring is simply not part of this platform.
    assert IOSDevice6.start_moniter is DeviceBase.start_moniter
    assert IOSDevice6.stop_moniter is DeviceBase.stop_moniter


def test_missing_ios4_binary_is_rejected(tmp_path: Path) -> None:
    with patch("idevice.device.ios6.device.ios4_binary", return_value=BINARY):
        with patch(
            "idevice.device.common.ios4cli.shutil.which", return_value=None
        ):
            with pytest.raises(IOSDevice6Error, match="CLI not found"):
                IOSDevice6(UDID, cache_dir=tmp_path / "cache")


def test_launch_app_opens_a_wda_session_with_args_and_environment(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout=INSTALLED_LISTING)
    ios6_device.launch_app(
        args=["-logLevel", "debug"], environment={"UNITY_DEBUG": "1"}
    )

    assert wda_client.sessions == [
        (APP_ID, ["-logLevel", "debug"], {"UNITY_DEBUG": "1"}, "accept")
    ]
    assert ios6_device.last_launch_pid == 4242
    # The only ios4 call is the installed-bundle check: no process_control.
    assert ios4_commands(ios6_device) == [
        [BINARY, "--udid", UDID, "application_listing"]
    ]


def test_launch_app_enables_the_wda_alert_monitor(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout=INSTALLED_LISTING)

    ios6_device.launch_app(APP_ID)

    # defaultAlertAction leaves WDA answering prompts for the whole session.
    assert wda_client.sessions[0][3] == AlertAction.ACCEPT


def test_accept_alert_selector_matches_only_affirmative_buttons() -> None:
    selector = build_accept_alert_selector()

    assert selector.startswith("**/XCUIElementTypeButton[`label IN {")
    for label in ("允许", "好", "Allow", "无线局域网与蜂窝网络"):
        assert f"'{label}'" in selector
    for label in ("不允许", "取消", "Cancel", "稍后"):
        assert label not in selector
    assert label_count(selector) == len(ACCEPT_ALERT_BUTTON_LABELS)


def test_accept_alert_selector_drops_duplicates_and_empty_labels() -> None:
    selector = build_accept_alert_selector(["允许", "", "允许", "好"])

    assert selector == "**/XCUIElementTypeButton[`label IN {'允许','好'}`]"


@pytest.mark.parametrize("labels", [[], [""], ["Don't Allow"], ["a`b"]])
def test_accept_alert_selector_rejects_unusable_labels(labels: list[str]) -> None:
    # A quote or backtick would break out of the class chain query.
    with pytest.raises(ValueError):
        build_accept_alert_selector(labels)


def test_launch_app_pins_the_accept_button_on_the_new_session(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout=INSTALLED_LISTING)

    ios6_device.launch_app(APP_ID)

    # facebook-wda drops defaultAlertAction, so the monitor is started by
    # posting autoClickAlertSelector on the session the launch created.
    assert wda_client.calls == ["session", "appium_settings"]
    selector = wda_client.settings[0][AUTO_CLICK_ALERT_SETTING]
    assert "'允许'" in selector
    # Accepting must never resolve to a deny button.
    assert "不允许" not in selector


def test_launch_app_honours_custom_accept_button_labels(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout=INSTALLED_LISTING)

    ios6_device.launch_app(APP_ID, accept_button_labels=["同意并继续"])

    assert wda_client.settings[0] == {
        AUTO_CLICK_ALERT_SETTING: (
            "**/XCUIElementTypeButton[`label IN {'同意并继续'}`]"
        )
    }


def test_launch_app_raises_when_the_accept_button_cannot_be_pinned(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout=INSTALLED_LISTING)
    wda_client.settings_error = RuntimeError("no session")

    with pytest.raises(IOSDevice6Error, match=AUTO_CLICK_ALERT_SETTING):
        ios6_device.launch_app(APP_ID)

    # The app is already up by then; reporting it beats letting the monitor
    # answer prompts with its positional guess.
    assert wda_client.sessions[0][0] == APP_ID
    assert ios6_device.last_launch_pid is None


def test_launch_app_can_leave_the_alert_monitor_off(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout=INSTALLED_LISTING)

    ios6_device.launch_app(APP_ID, alert_action=None)

    assert wda_client.sessions[0][3] is None
    # With no monitor running there is no accept button to pin.
    assert wda_client.settings == []


def test_launch_app_does_not_pin_an_accept_button_when_dismissing(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout=INSTALLED_LISTING)

    ios6_device.launch_app(APP_ID, alert_action=AlertAction.DISMISS)

    assert wda_client.sessions[0][3] == AlertAction.DISMISS
    assert wda_client.settings == []


def test_launch_app_dismisses_alerts_with_wda_not_polling(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout=INSTALLED_LISTING)
    wda_client.alert.exists = True

    ios6_device.launch_app(APP_ID)

    # No client-side alert scan happens: the device answers its own prompts.
    assert wda_client.queries == []


@pytest.mark.parametrize("pid", [None, True, 0, -1, "4242"])
def test_launch_app_keeps_pid_none_when_wda_omits_it(
    pid: object,
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout=INSTALLED_LISTING)
    ios6_device._last_launch_pid = 11
    ios6_device._last_launch_app_id = APP_ID
    wda_client.launch_pid = pid

    ios6_device.launch_app(APP_ID)

    # facebook-wda 1.5.4 has no session.pid; a missing value is not a
    # failed launch, only a missing handle for capture_memgraph.
    assert ios6_device.last_launch_pid is None
    assert ios6_device._last_launch_app_id == APP_ID
    assert "app_list" not in wda_client.calls


def test_launch_app_raises_instead_of_falling_back_to_ios4(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout=INSTALLED_LISTING)
    wda_client.session_error = RuntimeError("WDA unreachable")
    ios6_device._last_launch_pid = 11
    ios6_device._last_launch_app_id = APP_ID

    with pytest.raises(IOSDevice6Error, match="WDA failed to launch"):
        ios6_device.launch_app(APP_ID)

    assert all(
        "process_control" not in command
        for command in ios4_commands(ios6_device)
    )
    assert ios6_device.last_launch_pid is None
    assert ios6_device._last_launch_app_id == ""


def test_launch_app_rejects_an_app_that_is_not_installed(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout="")
    ios6_device._last_launch_pid = 11
    ios6_device._last_launch_app_id = APP_ID

    with pytest.raises(AppNotInstalledError, match=APP_ID):
        ios6_device.launch_app(APP_ID)

    assert wda_client.sessions == []
    assert ios6_device.last_launch_pid is None
    assert ios6_device._last_launch_app_id == ""


def test_launch_uses_ios4_process_control(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout="PID: 77\n")

    ios6_device.launch(RUNNER_BUNDLE_ID)

    assert ios4_commands(ios6_device) == [
        [BINARY, "--udid", UDID, "process_control", RUNNER_BUNDLE_ID]
    ]
    # process_control returns before the runner binds its port, so the launch
    # waits for WDA instead of letting the next request race its startup.
    assert wda_client.ready_waits == [WDA_READY_TIMEOUT]


def test_launch_raises_when_the_agent_never_becomes_ready(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout="PID: 77\n")
    wda_client.ready = False

    with pytest.raises(IOSDevice6Error, match="did not become ready"):
        ios6_device.launch(RUNNER_BUNDLE_ID, timeout=5)

    assert wda_client.ready_waits == [5]


def test_launch_can_skip_the_readiness_wait(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout="PID: 77\n")
    wda_client.ready = False

    ios6_device.launch(APP_ID, wait_ready=False)

    assert ios4_commands(ios6_device) == [
        [BINARY, "--udid", UDID, "process_control", APP_ID]
    ]
    assert wda_client.ready_waits == []


def test_starting_the_agent_is_left_to_the_caller(
    ios6_device: IOSDevice6,
) -> None:
    with pytest.raises(NotImplementedError, match="launch_wda"):
        ios6_device.launch_wda(RUNNER_BUNDLE_ID)


def test_tap_clicks_through_wda(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device.tap(0.5, 0.25, app_id=APP_ID)

    assert wda_client.clicks == [(0.5, 0.25)]
    ios6_device._ios4cli.runner.run.assert_not_called()


@pytest.mark.parametrize("bad", [-0.1, 1.5, float("nan"), True, "0.5"])
def test_tap_rejects_coordinates_outside_the_unit_square(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient, bad: object
) -> None:
    with pytest.raises(ValueError, match="normalized coordinate"):
        ios6_device.tap(bad, 0.5)  # type: ignore[arg-type]

    assert wda_client.clicks == []


def test_tap_raises_when_wda_rejects_the_click(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    wda_client.click_error = RuntimeError("no session")

    with pytest.raises(IOSDevice6Error, match="WDA failed to tap"):
        ios6_device.tap(0.5, 0.5)


def test_stop_app_uses_ios4_pkill_and_clears_the_launch_pid(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient
) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(stdout=INSTALLED_LISTING)
    ios6_device.launch_app(APP_ID)
    ios6_device._ios4cli.runner.run.return_value = result()

    ios6_device.stop_app()

    assert ios4_commands(ios6_device)[-1] == [
        BINARY,
        "--udid",
        UDID,
        "pkill",
        "--bundle",
        APP_ID,
    ]
    assert ios6_device.last_launch_pid is None
    assert wda_client.terminated == []


def test_stop_app_raises_when_pkill_fails(ios6_device: IOSDevice6) -> None:
    ios6_device._ios4cli.runner.run.return_value = result(
        returncode=1, stderr="no such process\n"
    )

    with pytest.raises(IOSDevice6Error, match="Failed to stop"):
        ios6_device.stop_app(APP_ID)


def test_install_uses_ideviceinstaller_and_caches_the_app(
    ios6_device: IOSDevice6, tmp_path: Path
) -> None:
    ipa = tmp_path / "ExampleGame.ipa"
    ipa.write_bytes(b"ipa")
    ios6_device._ios4cli.runner.run.return_value = result(
        stdout="Install: Complete\n"
    )

    with patch(
        "idevice.device.common.ios4cli.shutil.which",
        return_value=IDEVICEINSTALLER,
    ):
        assert ios6_device.install(ipa, app_id=APP_ID) is True

    ios6_device._ios4cli.runner.run.assert_called_once_with(
        [IDEVICEINSTALLER, "--udid", UDID, "install", str(ipa)],
        check=False,
        timeout=3600,
    )
    assert ios6_device._app_cache.get(APP_ID) is not None


def test_install_reports_failure_without_caching(
    ios6_device: IOSDevice6, tmp_path: Path
) -> None:
    ipa = tmp_path / "ExampleGame.ipa"
    ipa.write_bytes(b"ipa")
    ios6_device._ios4cli.runner.run.return_value = result(
        stderr="Install failed: invalid package\n"
    )

    assert ios6_device.install(ipa, app_id=APP_ID) is False
    assert ios6_device._app_cache.get(APP_ID) is None


def test_uninstall_drops_the_cache_entry(ios6_device: IOSDevice6) -> None:
    ios6_device._app_cache.add(APP_ID, version="1.0")
    ios6_device._ios4cli.runner.run.return_value = result()

    ios6_device.uninstall(APP_ID)

    assert ios4_commands(ios6_device) == [
        [BINARY, "--udid", UDID, "app_service", "uninstall", APP_ID]
    ]
    assert ios6_device._app_cache.get(APP_ID) is None


def test_get_installed_pkg_name_needs_the_app_present(
    ios6_device: IOSDevice6,
) -> None:
    ios6_device._app_cache.add(APP_ID, version="1.0")

    ios6_device._ios4cli.runner.run.return_value = result(stdout="")
    assert ios6_device.get_installed_pkg_name(APP_ID) is None

    ios6_device._ios4cli.runner.run.return_value = result(stdout=INSTALLED_LISTING)
    info = ios6_device.get_installed_pkg_name(APP_ID)
    assert info is not None and info.version == "1.0"


def test_screenshot_uses_the_ios4_screenshot_service(
    ios6_device: IOSDevice6, tmp_path: Path
) -> None:
    output = tmp_path / "shots" / "screen.png"

    def capture(command: list[str], **_kwargs: object) -> CommandResult:
        Path(command[-1]).write_bytes(b"\x89PNG")
        return result()

    ios6_device._ios4cli.runner.run.side_effect = capture

    assert ios6_device.screenshot(output) is True

    command = ios4_commands(ios6_device)[0]
    assert command[:4] == [BINARY, "--udid", UDID, "screenshot"]
    assert output.read_bytes() == b"\x89PNG"


def test_capture_memgraph_needs_a_pid(ios6_device: IOSDevice6, tmp_path: Path) -> None:
    with pytest.raises(IOSDevice6Error, match="No PID available"):
        ios6_device.capture_memgraph(tmp_path / "game.memgraph")

    ios6_device._ios4cli.runner.run.assert_not_called()


def test_capture_memgraph_shells_out_to_ios4(
    ios6_device: IOSDevice6, tmp_path: Path
) -> None:
    output = tmp_path / "game.memgraph"

    def capture(command: list[str], **_kwargs: object) -> CommandResult:
        Path(command[-1]).write_bytes(b"memgraph")
        return result()

    ios6_device._ios4cli.runner.run.side_effect = capture

    assert ios6_device.capture_memgraph(output, pid=4242) == output.resolve()

    command = ios4_commands(ios6_device)[0]
    assert command[:5] == [BINARY, "--udid", UDID, "memgraph", "4242"]
    assert output.read_bytes() == b"memgraph"


def test_capture_memgraph_uses_the_pid_returned_by_wda(
    ios6_device: IOSDevice6, wda_client: FakeWDAClient, tmp_path: Path
) -> None:
    output = tmp_path / "launched-game.memgraph"
    ios6_device._ios4cli.runner.run.return_value = result(
        stdout=INSTALLED_LISTING
    )
    wda_client.launch_pid = 8675
    ios6_device.launch_app(APP_ID)

    def capture(command: list[str], **_kwargs: object) -> CommandResult:
        Path(command[-1]).write_bytes(b"memgraph")
        return result()

    ios6_device._ios4cli.runner.run.side_effect = capture

    assert ios6_device.capture_memgraph(output) == output.resolve()
    command = ios4_commands(ios6_device)[-1]
    assert command[:5] == [BINARY, "--udid", UDID, "memgraph", "8675"]


def afc_command(*arguments: str) -> list[str]:
    """Build the ``afc --documents`` command the device is expected to run."""
    return [BINARY, "--udid", UDID, "afc", "--documents", APP_ID, *arguments]


def file_info(ifmt: str) -> CommandResult:
    """Fake ``afc info`` output for a directory or regular file."""
    return result(stdout=f'FileInfo {{\n    st_ifmt: "{ifmt}",\n}}\n')


MISSING = result(
    returncode=134, stderr="Failed to get file info: Afc(ObjectNotFound)\n"
)


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


def test_documents_exists_uses_the_ios4_afc_service(
    ios6_device: IOSDevice6,
) -> None:
    ios6_device._ios4cli.runner.run.return_value = file_info("S_IFREG")

    assert ios6_device.documents_exists(APP_ID, "Logs/app.log") is True

    ios6_device._ios4cli.runner.run.assert_called_once_with(
        afc_command("info", "/Documents/Logs/app.log"), check=False
    )


def test_documents_ls_lists_directory_entries(ios6_device: IOSDevice6) -> None:
    ios6_device._ios4cli.runner.run.side_effect = route(
        {
            "info /Documents/Logs": file_info("S_IFDIR"),
            "list /Documents/Logs": listing("app.log", "旧日志"),
        }
    )

    assert ios6_device.documents_ls(APP_ID, "Logs") == ["app.log", "旧日志"]


def test_documents_pull_downloads_a_file(
    ios6_device: IOSDevice6, tmp_path: Path
) -> None:
    dest = tmp_path / "out" / "app.log"
    ios6_device._ios4cli.runner.run.side_effect = route(
        {
            "info /Documents/Logs/app.log": file_info("S_IFREG"),
            "download /Documents/Logs/app.log": result(),
        }
    )

    assert ios6_device.documents_pull(APP_ID, "Logs/app.log", dest) is True

    ios6_device._ios4cli.runner.run.assert_called_with(
        afc_command("download", "/Documents/Logs/app.log", str(dest)), check=False
    )


def test_documents_push_uploads_a_file(
    ios6_device: IOSDevice6, tmp_path: Path
) -> None:
    local = tmp_path / "app.log"
    local.write_text("log")
    ios6_device._ios4cli.runner.run.side_effect = route(
        {
            "info /Documents/Logs/app.log": MISSING,
            "mkdir /Documents/Logs": result(),
            "upload " + str(local): result(),
        }
    )

    assert ios6_device.documents_push(APP_ID, local, "Logs/app.log") is True

    ios6_device._ios4cli.runner.run.assert_called_with(
        afc_command("upload", str(local), "/Documents/Logs/app.log"), check=False
    )


def test_documents_rm_removes_files_and_trees(ios6_device: IOSDevice6) -> None:
    ios6_device._ios4cli.runner.run.side_effect = route(
        {
            "info /Documents/Logs": file_info("S_IFDIR"),
            "remove_all /Documents/Logs": result(),
        }
    )

    assert ios6_device.documents_rm(APP_ID, "Logs") is True

    ios6_device._ios4cli.runner.run.assert_called_with(
        afc_command("remove_all", "/Documents/Logs"), check=False
    )


def test_documents_helpers_validate_arguments(ios6_device: IOSDevice6) -> None:
    with pytest.raises(ValueError, match="app_id is required"):
        ios6_device.documents_exists("", "Logs")
    with pytest.raises(ValueError, match="remote is required"):
        ios6_device.documents_ls(APP_ID, "")


def test_generic_transfers_are_unsupported(
    ios6_device: IOSDevice6, tmp_path: Path
) -> None:
    with pytest.raises(NotImplementedError, match="push"):
        ios6_device.push(tmp_path / "a", "/tmp/a")
    with pytest.raises(NotImplementedError, match="pull"):
        ios6_device.pull("/tmp/a", tmp_path / "a")
    with pytest.raises(NotImplementedError, match="ls"):
        ios6_device.ls("/tmp")
    with pytest.raises(NotImplementedError, match="pull2"):
        ios6_device.pull2(AppDataPath.Persistent, "Logs", tmp_path / "out")
    with pytest.raises(NotImplementedError, match="delete2"):
        ios6_device.delete2(AppDataPath.Persistent, "Logs")


def test_default_udid_reads_ideviceinfo() -> None:
    output = 'UniqueDeviceID: String(\n    "ABCDEF",\n)\n'
    runner = MagicMock()
    runner.run.return_value = result(stdout=output)

    with patch("idevice.device.ios6.device.ios4_binary", return_value=BINARY):
        with patch(
            "idevice.device.ios6.device.SubprocessRunner", return_value=runner
        ):
            assert IOSDevice6.default_udid() == "ABCDEF"


def test_from_env_binds_the_gauto_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GAUTO_DEVICE_UDID", UDID)
    monkeypatch.setenv("GAUTO_DEVICE_IP", DEVICE_IP)
    monkeypatch.setenv("GAUTO_PACKAGE_NAME", APP_ID)
    monkeypatch.setenv("IDEVICE_IOS4_BINARY", BINARY)
    monkeypatch.setattr(
        "idevice.device.common.ios4cli.shutil.which", lambda _name: BINARY
    )
    monkeypatch.setattr(
        "idevice.device.config.user_data_dir", lambda: tmp_path / "cache"
    )

    device = IOSDevice6.from_env()

    assert device.device_id == UDID
    assert device.device_ip == DEVICE_IP
    assert device.package_name == APP_ID
