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


def test_launch_uses_wda_first_and_keeps_the_session_open(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.return_value = result(
        stdout=f"  {APP_ID}  ExampleGame  1.0\n"
    )
    wda_session = MagicMock()
    wda_session.app_list.return_value = [
        {"pid": 52, "bundleId": "com.apple.springboard"},
        {"pid": 4815, "bundleId": APP_ID},
    ]
    wda_client = MagicMock()
    wda_client.session.return_value = wda_session

    with patch(
        "idevice.device.ios4.device.wda.Client", return_value=wda_client
    ) as client:
        ios4_device.launch_app(
            APP_ID,
            args=["--mode", "debug"],
            environment={"MallocStackLogging": "1"},
        )

    client.assert_called_once_with(None)
    wda_client.session.assert_called_once_with(
        APP_ID,
        arguments=["--mode", "debug"],
        environment={"MallocStackLogging": "1"},
    )
    wda_session.close.assert_not_called()
    wda_session.__exit__.assert_not_called()
    assert ios4_device.last_launch_pid == 4815
    assert ios4_device._last_launch_app_id == APP_ID
    assert ios4_device._runner.run.call_count == 1
    assert ios4_device._runner.run.call_args.args[0] == [
        BINARY,
        "--udid",
        UDID,
        "application_listing",
    ]


def test_launch_via_wda_leaves_pid_unset_when_wda_reports_none(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.return_value = result(
        stdout=f"  {APP_ID}  ExampleGame  1.0\n"
    )
    wda_session = MagicMock()
    wda_session.app_list.return_value = [
        {"pid": 52, "bundleId": "com.apple.springboard"}
    ]
    wda_client = MagicMock()
    wda_client.session.return_value = wda_session

    with patch(
        "idevice.device.ios4.device.wda.Client", return_value=wda_client
    ):
        ios4_device.launch_app(APP_ID)

    wda_client.session.assert_called_once_with(
        APP_ID, arguments=None, environment=None
    )
    assert ios4_device.last_launch_pid is None
    assert ios4_device._last_launch_app_id == APP_ID
    assert ios4_device._runner.run.call_count == 1


def test_launch_addresses_wda_on_the_bound_device_ip(tmp_path: Path) -> None:
    with patch("idevice.device.ios4.device.ios4_binary", return_value=BINARY):
        with patch(
            "idevice.device.ios4.device.shutil.which", return_value=BINARY
        ):
            device = IOSDevice4(
                UDID,
                device_ip="10.0.0.5",
                package_name=APP_ID,
                cache_dir=tmp_path / "cache",
            )
    device._runner = MagicMock()
    device._runner.run.return_value = result(
        stdout=f"  {APP_ID}  ExampleGame  1.0\n"
    )

    with patch("idevice.device.ios4.device.wda.Client") as client:
        client.return_value.session.return_value.app_list.return_value = []
        device.launch_app(APP_ID)

    client.assert_called_once_with("http://10.0.0.5:8100")


def test_launch_falls_back_to_process_control_when_wda_fails(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.side_effect = [
        result(stdout=f"  {APP_ID}  ExampleGame  1.0\n"),
        result(stdout="PID: 4815\n"),
    ]

    with patch(
        "idevice.device.ios4.device.wda.Client",
        side_effect=RuntimeError("wda unreachable"),
    ):
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

    with patch(
        "idevice.device.ios4.device.wda.Client",
        side_effect=RuntimeError("wda unreachable"),
    ):
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

    with patch("idevice.device.ios4.device.wda.Client") as client:
        with pytest.raises(ValueError, match="app_id is required"):
            device.launch_app()

    device._runner.run.assert_not_called()
    client.assert_not_called()


def test_launch_rejects_uninstalled_app(ios4_device: IOSDevice4) -> None:
    ios4_device._runner.run.return_value = result(
        stdout="Found 0 applications:\n"
    )

    with patch("idevice.device.ios4.device.wda.Client") as client:
        with pytest.raises(AppNotInstalledError, match="App not installed"):
            ios4_device.launch_app(APP_ID)

    assert ios4_device._runner.run.call_count == 1
    client.assert_not_called()


def test_launch_validates_encoding_before_reaching_wda(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.return_value = result(
        stdout=f"  {APP_ID}  ExampleGame  1.0\n"
    )

    with patch("idevice.device.ios4.device.wda.Client") as client:
        with pytest.raises(ValueError, match="cannot be empty"):
            ios4_device.launch_app(APP_ID, args=["--mode", ""])

    client.assert_not_called()


def test_launch_requires_pid_in_process_control_output(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._runner.run.side_effect = [
        result(stdout=f"  {APP_ID}  ExampleGame  1.0\n"),
        result(stdout="launch completed without pid\n"),
    ]

    with patch(
        "idevice.device.ios4.device.wda.Client",
        side_effect=RuntimeError("wda unreachable"),
    ):
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


def test_stop_uses_wda_first_and_clears_the_tracked_pid(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._last_launch_pid = 4815
    ios4_device._last_launch_app_id = APP_ID
    wda_client = MagicMock()

    with patch(
        "idevice.device.ios4.device.wda.Client", return_value=wda_client
    ) as client:
        ios4_device.stop_app()

    client.assert_called_once_with(None)
    wda_client.app_terminate.assert_called_once_with(APP_ID)
    # Opening a session displaces the app under test and closing one
    # terminates it, so neither may happen around an app operation.
    wda_client.session.assert_not_called()
    wda_client.close.assert_not_called()
    ios4_device._runner.run.assert_not_called()
    assert ios4_device.last_launch_pid is None


def test_stop_falls_back_to_ios4_when_wda_fails(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._device_ip = "192.0.2.10"
    ios4_device._last_launch_pid = 4815
    ios4_device._last_launch_app_id = APP_ID
    ios4_device._runner.run.return_value = result(stdout="Killed 4815 (Game)\n")

    with patch(
        "idevice.device.ios4.device.wda.Client",
        side_effect=RuntimeError("WDA unavailable"),
    ) as client:
        ios4_device.stop_app()

    client.assert_called_once_with("http://192.0.2.10:8100")
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


def test_stop_falls_back_to_pkill_when_wda_termination_fails(
    ios4_device: IOSDevice4,
) -> None:
    wda_client = MagicMock()
    wda_client.app_terminate.side_effect = RuntimeError("termination failed")
    ios4_device._runner.run.return_value = result(stdout="Killed 4815 (Game)\n")

    with patch("idevice.device.ios4.device.wda.Client", return_value=wda_client):
        ios4_device.stop_app()

    wda_client.close.assert_not_called()
    ios4_device._runner.run.assert_called_once_with(
        [BINARY, "--udid", UDID, "pkill", "--bundle", APP_ID],
        check=False,
    )


def test_stop_keeps_the_tracked_pid_of_another_app(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._last_launch_pid = 4815
    ios4_device._last_launch_app_id = APP_ID
    wda_client = MagicMock()

    with patch("idevice.device.ios4.device.wda.Client", return_value=wda_client):
        ios4_device.stop_app("com.example.other")

    wda_client.app_terminate.assert_called_once_with("com.example.other")
    assert ios4_device.last_launch_pid == 4815


def test_stop_raises_when_pkill_fails(ios4_device: IOSDevice4) -> None:
    ios4_device._runner.run.return_value = result(
        returncode=1, stderr="No installed application with bundle ID\n"
    )

    with patch(
        "idevice.device.ios4.device.wda.Client",
        side_effect=RuntimeError("WDA unavailable"),
    ):
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


def test_tap_uses_wda_normalized_coordinates_on_the_live_session(
    ios4_device: IOSDevice4,
) -> None:
    ios4_device._device_ip = "192.0.2.10"
    wda_client = MagicMock()

    with patch(
        "idevice.device.ios4.device.wda.Client", return_value=wda_client
    ) as client:
        ios4_device.tap(0, 1, app_id=APP_ID)

    client.assert_called_once_with("http://192.0.2.10:8100")
    wda_client.click.assert_called_once_with(0.0, 1.0)
    # A tap that opened and closed its own session would kill the app it
    # was tapping on.
    wda_client.session.assert_not_called()
    wda_client.close.assert_not_called()
    ios4_device._runner.run.assert_not_called()


@pytest.mark.parametrize(
    ("x", "y", "coordinate"),
    [
        (-0.1, 0.5, "x"),
        (1.1, 0.5, "x"),
        (0.5, -0.1, "y"),
        (0.5, 1.1, "y"),
        (True, 0.5, "x"),
    ],
)
def test_tap_rejects_invalid_normalized_coordinates(
    ios4_device: IOSDevice4,
    x: float,
    y: float,
    coordinate: str,
) -> None:
    with patch("idevice.device.ios4.device.wda.Client") as client:
        with pytest.raises(
            ValueError, match=rf"{coordinate} must be a normalized coordinate"
        ):
            ios4_device.tap(x, y)

    client.assert_not_called()


def test_tap_wraps_wda_failure(
    ios4_device: IOSDevice4,
) -> None:
    wda_client = MagicMock()
    wda_client.click.side_effect = RuntimeError("tap failed")

    with patch("idevice.device.ios4.device.wda.Client", return_value=wda_client):
        with pytest.raises(IOSDevice4Error, match="WDA failed to tap"):
            ios4_device.tap(0.5, 0.25)

    wda_client.close.assert_not_called()


def test_dismiss_message_popup_clicks_known_button_when_alert_is_present(
    ios4_device: IOSDevice4,
) -> None:
    wda_client = MagicMock()
    wda_alert = MagicMock()
    wda_alert.exists = True
    wda_client.alert = wda_alert

    allow_button = MagicMock()
    allow_button.exists = True
    close_button = MagicMock()
    close_button.exists = False

    def selector(**kwargs: object) -> MagicMock:
        if kwargs.get("text") == "Allow":
            return allow_button
        if kwargs.get("text") == "取消":
            return close_button
        return MagicMock()

    wda_client.side_effect = selector

    with patch(
        "idevice.device.ios4.device.wda.Client", return_value=wda_client
    ) as client:
        assert ios4_device.dismiss_message_popup(
            button_labels=["Allow", "取消"], timeout=0.1
        ) is True

    client.assert_called_once_with(None)
    wda_client.session.assert_not_called()
    wda_client.close.assert_not_called()
    allow_button.click.assert_called_once_with()
    close_button.click.assert_not_called()


def test_dismiss_message_popup_clicks_network_button_by_default_labels(
    ios4_device: IOSDevice4,
) -> None:
    wda_client = MagicMock()
    wda_alert = MagicMock()
    wda_alert.exists = True
    wda_client.alert = wda_alert

    network_button = MagicMock()
    network_button.exists = True
    allow_button = MagicMock()
    allow_button.exists = True
    other_button = MagicMock()
    other_button.exists = False

    def any_button(**_: object) -> MagicMock:
        return other_button

    def selector(**kwargs: object) -> MagicMock:
        if kwargs.get("text") == "Allow Access to Local Network":
            return network_button
        if kwargs.get("text") == "允许":
            return allow_button
        return any_button(**kwargs)

    wda_client.side_effect = selector

    with patch(
        "idevice.device.ios4.device.wda.Client", return_value=wda_client
    ) as client:
        assert ios4_device.dismiss_message_popup(timeout=0.1) is True

    client.assert_called_once_with(None)
    wda_client.session.assert_not_called()
    wda_client.close.assert_not_called()
    network_button.click.assert_called_once_with()
    allow_button.click.assert_not_called()


def test_dismiss_message_popup_returns_false_when_alert_not_present(
    ios4_device: IOSDevice4,
) -> None:
    wda_client = MagicMock()
    wda_alert = MagicMock()
    wda_alert.exists = False
    wda_client.alert = wda_alert

    with patch(
        "idevice.device.ios4.device.wda.Client", return_value=wda_client
    ) as client:
        assert ios4_device.dismiss_message_popup(button_labels=["OK"]) is False

    client.assert_called_once_with(None)
    wda_client.session.assert_not_called()
    wda_client.close.assert_not_called()


def test_dismiss_message_popup_raises_on_wda_error(
    ios4_device: IOSDevice4,
) -> None:
    with patch(
        "idevice.device.ios4.device.wda.Client",
        side_effect=RuntimeError("wda unreachable"),
    ) as client:
        with pytest.raises(IOSDevice4Error, match="failed to handle message popup"):
            ios4_device.dismiss_message_popup(button_labels=["OK"])

    client.assert_called_once_with(None)


def test_dismiss_message_popup_rejects_empty_labels() -> None:
    with pytest.raises(ValueError, match="at least one button label is required"):
        IOSDevice4._normalize_message_popup_labels(())


def test_dismiss_message_popup_with_duration_starts_background_thread(
    ios4_device: IOSDevice4,
) -> None:
    thread = MagicMock()

    with patch(
        "idevice.device.ios4.device.threading.Thread", return_value=thread
    ) as thread_factory:
        assert ios4_device.dismiss_message_popup(
            button_labels=["OK"], duration=5.0, interval=0.5, timeout=0.3
        ) is True

    thread_factory.assert_called_once_with(
        target=ios4_device._watch_message_popups,
        args=(("OK",), 0.3, 5.0, 0.5),
        daemon=True,
    )
    thread.start.assert_called_once_with()


def test_dismiss_message_popup_with_duration_rejects_non_positive_values(
    ios4_device: IOSDevice4,
) -> None:
    with pytest.raises(ValueError, match="duration must be a positive number"):
        ios4_device.dismiss_message_popup(duration=0)

    with pytest.raises(ValueError, match="interval must be a positive number"):
        ios4_device.dismiss_message_popup(duration=5.0, interval=0)

    with pytest.raises(ValueError, match="timeout must be a positive number"):
        ios4_device.dismiss_message_popup(duration=5.0, timeout=0)


def test_watch_message_popups_dismisses_until_duration_elapses(
    ios4_device: IOSDevice4,
) -> None:
    wda_client = MagicMock()
    wda_alert = MagicMock()
    wda_alert.exists = True
    wda_client.alert = wda_alert

    ok_button = MagicMock()
    ok_button.exists = True
    wda_client.side_effect = lambda **_: ok_button

    clock = iter([0.0, 0.5, 1.5])
    with patch(
        "idevice.device.ios4.device.wda.Client", return_value=wda_client
    ), patch("idevice.device.ios4.device.time.monotonic", side_effect=clock), patch(
        "idevice.device.ios4.device.time.sleep"
    ) as sleep:
        ios4_device._watch_message_popups(("OK",), 0.1, duration=1.0, interval=0.5)

    assert ok_button.click.call_count == 2
    sleep.assert_called_once_with(0.5)
    # The watch ran twice without ever creating or deleting a session: doing
    # so on each scan is what used to terminate the app under test.
    wda_client.session.assert_not_called()
    wda_client.close.assert_not_called()


def test_watch_message_popups_logs_and_returns_when_session_fails(
    ios4_device: IOSDevice4,
) -> None:
    with patch(
        "idevice.device.ios4.device.wda.Client",
        side_effect=RuntimeError("wda unreachable"),
    ):
        ios4_device._watch_message_popups(("OK",), 0.1, duration=1.0, interval=0.5)


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
