# idevice

Cross-platform device automation for end-to-end test workflows: install and manage apps on physical devices, transfer files, and drive UI interactions through a small, platform-agnostic API.

## Platform status

| Platform | Backend | App lifecycle | File transfer | Documents sandbox | Swipe | UI automation |
|----------|---------|---------------|---------------|-------------------|-------|---------------|
| iOS | [go-ios](https://github.com/danielpaulus/go-ios) (`IOSDevice`) | Yes | Yes | — | — | Planned (WDA) |
| iOS | [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (`IOSDevice3`) | Yes | Yes (AFC + app sandbox) | Yes | — | Planned (WDA) |
| Android | adb (`AndroidDevice`) | Yes | Yes | — | Yes | Yes (`AndroidUIAuto`) |
| Windows | PowerShell (`WindowsDevice`) | Partial | — | — | — | Planned |

macOS and HarmonyOS are not implemented yet.

## Requirements

- Python >= 3.9
- Platform CLI tools on `PATH` (or configured via environment variables below):
  - **iOS (go-ios):** `ios`
  - **iOS (pymobiledevice3):** `pymobiledevice3` (default: `/opt/ios3/bin/pymobiledevice3` on Unix, `~/ios3/bin/pymobiledevice3.exe` on Windows)
  - **Android:** `adb`

Python packages `pymobiledevice3` and `uiautomator2` are installed automatically with the project (see [Install](#install)). `IOSDevice3` uses the pymobiledevice3 Python library for Documents sandbox access (`documents_*`); other iOS operations go through the CLI.

## Install

```bash
uv sync
# or, with dev dependencies
uv sync --group dev
```

## Quick start

Create a device bound to a single UDID or serial, then call lifecycle methods:

```python
from pathlib import Path

from idevice.device import Device, Platform

# iOS via go-ios
device = Device.create(Platform.IOS, device_id="00000000-0000000000000000", device_ip="")

# iOS via pymobiledevice3 (iOS 17+ tunnel support)
device = Device.create(Platform.IOS3, device_id="00000000-0000000000000000", device_ip="")

# Android via adb
device = Device.create(Platform.ANDROID, device_id="emulator-5554", device_ip="")

device.install(Path("MyApp.ipa"), app_id="com.example.app")
device.launch_app("com.example.app")
device.is_installed("com.example.app")
device.stop_app("com.example.app")
device.uninstall("com.example.app")
```

Android swipe (via `adb shell input swipe`):

```python
device.swipe(100, 800, 100, 200, duration_ms=300)
```

iOS Documents sandbox (`IOSDevice3` only — requires file-sharing entitlements):

```python
device.documents_push("com.example.app", Path("log.txt"), "Logs/log.txt")
device.documents_exists("com.example.app", "Logs/log.txt")
device.documents_ls("com.example.app", "Logs")
device.documents_pull("com.example.app", "Logs", Path("out/Logs"))
device.documents_rm("com.example.app", "Logs/log.txt")
```

## Examples

Runnable scripts under `examples/` auto-detect the first connected device when no ID is passed:

```bash
# Android: launch, push/pull, swipe (optional --apk / --package)
uv run python examples/android_device.py

# iOS (pymobiledevice3): lifecycle, AFC, app sandbox, Documents API
uv run python examples/ios3_device.py

# Install an IPA and exercise sandbox file transfer
uv run python examples/ios3_device.py \
  --ipa path/to/app.ipa \
  --app-id com.example.app \
  --sandbox-app-id com.example.app
```

See the module docstrings in each example for prerequisites (Developer Mode, iOS 17+ tunnel, USB debugging, etc.).

## API overview

### `DeviceBase`

Every platform implementation shares the same interface:

- `install(package_path, app_id=None)` — install `.ipa` / `.apk` and optionally record bundle id → file name
- `uninstall(app_id)` / `is_installed(app_id)` / `get_installed_pkg_name(app_id)`
- `launch_app(app_id)` / `stop_app(app_id)`
- `push(local, remote, app_id=None, documents_only=False)` / `pull(remote, local, app_id=None, documents_only=True)` — host ↔ device file transfer
- `ls(remote, app_id=None, recursive=False)` — list a remote directory on the device
- `documents_exists(app_id, remote)` / `documents_ls(app_id, remote)` / `documents_push(app_id, local, remote)` / `documents_pull(app_id, remote, local)` / `documents_rm(app_id, remote)` — app Documents sandbox (implemented on `IOSDevice3`; other platforms raise `NotImplementedError`)
- `swipe(x1, y1, x2, y2, duration_ms=300)` — touch gesture (Android implemented; iOS/Windows raise `NotImplementedError`)
- `host_is_running()` — whether WebDriverAgent / UIAutomator2 host process is up

Use `Device.create(Platform, device_id=…, device_ip="")` or construct `IOSDevice`, `IOSDevice3`, `AndroidDevice`, or `WindowsDevice` directly.

### `UIAutoBase`

Higher-level UI helpers built on top of device tooling. Currently only `AndroidUIAuto` is available (`swipe`, `dismiss_post_install_dialogs`, hierarchy access).

### iOS backends

**`IOSDevice` (go-ios)** — lightweight CLI wrapper around go-ios for install, launch, and AFC transfers.

**`IOSDevice3` (pymobiledevice3)** — uses pymobiledevice3 services:

- App install/uninstall/list via `apps`
- Process control via `developer dvt launch` / `pkill`
- File transfer via `afc push/pull` or `apps push/pull` (app sandbox, with optional `--documents`)
- Documents sandbox via the pymobiledevice3 Python library (House Arrest AFC): `documents_exists`, `documents_ls`, `documents_push`, `documents_pull`, `documents_rm`
- Developer-mode commands require a mounted DeveloperDiskImage; on iOS 17+ an active tunnel is required (`pymobiledevice3 remote start-tunnel`)

Choose `Platform.IOS` or `Platform.IOS3` depending on which CLI you have deployed.

## Configuration

Environment variables override default binary paths:

| Variable | Default | Used by |
|----------|---------|---------|
| `IDEVICE_IOS_BINARY` | `ios` | `IOSDevice` |
| `IDEVICE_IOS3_BINARY` | `/opt/ios3/bin/pymobiledevice3` (Unix) / `~/ios3/bin/pymobiledevice3.exe` (Windows) | `IOSDevice3` |
| `IDEVICE_ADB_BINARY` | `adb` | `AndroidDevice`, `AndroidUIAuto` |
| `IDEVICE_POWERSHELL_BINARY` | `powershell` | `WindowsDevice` |

User data (e.g. installed-app cache) is stored under `~/.idevice` by default.

## Testing

Unit tests run without a connected device:

```bash
uv run pytest
```

Integration tests under `tests/device/` require a physical iOS device and pymobiledevice3. They are excluded by default; run with:

```bash
export IDEVICE_IOS3_UDID="00000000-0000000000000000"
uv run pytest -m integration tests/device/
```

See `tests/device/conftest.py` for optional variables (`IDEVICE_IOS3_TEST_IPA`, sandbox push/pull settings, etc.).

## Development

```bash
uv run ruff check src tests
uv run pytest
```
