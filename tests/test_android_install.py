"""Unit tests for Android APK installation and OEM prompt handling."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from idevice.device.android.device import AndroidDevice, InstallResult


class FakeWatchContext:
    """Record WatchContext rules without starting background threads."""

    def __init__(self, *, builtin: bool) -> None:
        self.builtin = builtin
        self.pending: list[str] = []
        self.click_rules: list[tuple[str, ...]] = []
        self.call_rules: list[tuple[tuple[str, ...], object]] = []
        self.started = False

    def __enter__(self) -> FakeWatchContext:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def when(self, xpath: str) -> FakeWatchContext:
        self.pending.append(xpath)
        return self

    def click(self) -> None:
        self.click_rules.append(tuple(self.pending))
        self.pending.clear()

    def call(self, callback: object) -> None:
        self.call_rules.append((tuple(self.pending), callback))
        self.pending.clear()

    def start(self) -> None:
        self.started = True


class FakeUiDevice:
    def __init__(self) -> None:
        self.contexts: list[FakeWatchContext] = []

    def watch_context(self, *, builtin: bool, autostart: bool) -> FakeWatchContext:
        assert autostart is False
        context = FakeWatchContext(builtin=builtin)
        self.contexts.append(context)
        return context


def make_device(monkeypatch, tmp_path: Path) -> AndroidDevice:
    monkeypatch.setattr("idevice.device.android.device.shutil.which", lambda _name: "/usr/bin/adb")
    return AndroidDevice("serial-1", cache_dir=tmp_path / "cache")


def test_install_without_app_id_does_not_uninstall(monkeypatch, tmp_path: Path) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    device = make_device(monkeypatch, tmp_path)
    uninstall = MagicMock()
    monkeypatch.setattr(device, "uninstall", uninstall)
    monkeypatch.setattr(
        device,
        "_install_with_uiautomator2",
        lambda *_args, **_kwargs: InstallResult(True, 0, "Success", ""),
    )

    assert device.install(apk) is True
    uninstall.assert_not_called()


def test_install_retries_vivo_risk_acknowledgement_until_checked(monkeypatch, tmp_path: Path) -> None:
    device = make_device(monkeypatch, tmp_path)
    ui_device = FakeUiDevice()
    connected_ids: list[str | None] = []

    def connect(device_id: str | None = None) -> FakeUiDevice:
        connected_ids.append(device_id)
        return ui_device

    monkeypatch.setitem(sys.modules, "uiautomator2", SimpleNamespace(connect=connect))
    monkeypatch.setattr(
        "idevice.device.android.device.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="Success", stderr=""),
    )
    monkeypatch.setattr(device, "_dismiss_post_install_popups", lambda _ui: None)

    result = device._install_with_uiautomator2(["adb", "install", "app.apk"], device_id="serial-1")

    assert result.ok is True
    assert connected_ids == ["serial-1"]
    assert len(ui_device.contexts) == 2
    risk_context, builtin_context = ui_device.contexts
    assert risk_context.builtin is False
    assert risk_context.started is True
    assert [rule for rule, _callback in risk_context.call_rules] == [
        ("已了解应用的风险检测结果",),
        ("我已了解应用的风险检测结果",),
    ]
    assert builtin_context.builtin is True
    assert builtin_context.started is True

    unchecked_element = MagicMock()
    unchecked_element.attrib = {"checked": "false"}
    checked_element = MagicMock()
    checked_element.attrib = {"checked": "true"}
    callback = risk_context.call_rules[0][1]
    callback(unchecked_element)
    callback(unchecked_element)
    callback(checked_element)
    assert unchecked_element.click.call_count == 2
    checked_element.click.assert_not_called()
