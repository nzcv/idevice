# idevice

Cross-platform device automation for end-to-end test workflows: install and manage apps on physical devices, transfer files, and drive UI interactions through a small, platform-agnostic API.

The package ships two complementary APIs:

- **`idevice.device`** — local device automation: talk to a USB/network-attached device through platform CLIs (go-ios, pymobiledevice3, adb) to install apps, transfer files, and drive UI.
- **`idevice.host`** — keeper-backed remote orchestration: drive a memory-measurement run on a host machine that talks to the EndlessKeeper control server and the on-device RemoteControlTest runner over HTTP.

## Platform status

| Platform | Backend | App lifecycle | File transfer | Documents sandbox | Swipe | UI automation |
|----------|---------|---------------|---------------|-------------------|-------|---------------|
| iOS | [go-ios](https://github.com/danielpaulus/go-ios) (`IOSDevice`) | Yes | Yes | — | — | Planned (WDA) |
| iOS | [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (`IOSDevice3`) | Yes | Yes (AFC + app sandbox) | Yes | — | Planned (WDA) |
| Android | adb (`AndroidDevice`) | Yes | Yes | — | Yes | Yes (`AndroidUIAuto`) |
| Windows | PowerShell (`WindowsDevice`) | Partial | — | Yes (local filesystem) | — | Planned |

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

Windows Documents sandbox (`WindowsDevice` — backed by the local filesystem under
`%LocalAppData%/<company_name>/<package_name>`). The sandbox root is fixed at
construction, so `company_name` and `package_name` are required; `remote` is
always resolved relative to that root, and every method works on both files and
directories:

```python
device = Device.create(
    Platform.WINDOWS,
    device_id="MY-PC",
    device_ip="",
    company_name="MyCompany",
    package_name="MyApp",
)

device.documents_push("MyApp.exe", Path("log.txt"), "Logs/log.txt")
device.documents_push("MyApp.exe", Path("assets"), "assets")  # whole directory
device.documents_exists("MyApp.exe", "Logs/log.txt")
device.documents_ls("MyApp.exe", "Logs")
device.documents_pull("MyApp.exe", "Logs", Path("out/Logs"))
device.documents_rm("MyApp.exe", "Logs")
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

# Host orchestration: keeper-backed memory-measurement run (see "Host orchestration")
uv run python examples/host_example.py --from-env --bundle-id com.example.app
```

See the module docstrings in each example for prerequisites (Developer Mode, iOS 17+ tunnel, USB debugging, EndlessKeeper reachability, etc.).

## API overview

### `DeviceBase`

Every platform implementation shares the same interface:

- `install(package_path, app_id=None)` — install `.ipa` / `.apk` and optionally record bundle id → file name
- `uninstall(app_id)` / `is_installed(app_id)` / `get_installed_pkg_name(app_id)` — the latter returns an `InstalledAppInfo(app_id, version, path)` or `None`
- `launch_app(app_id)` / `stop_app(app_id)`
- `push(local, remote, app_id=None, documents_only=False)` / `pull(remote, local, app_id=None, documents_only=True)` — host ↔ device file transfer
- `ls(remote, app_id=None, recursive=False)` — list a remote directory on the device
- `documents_exists(app_id, remote)` / `documents_ls(app_id, remote)` / `documents_push(app_id, local, remote)` / `documents_pull(app_id, remote, local)` / `documents_rm(app_id, remote)` — app Documents sandbox, supporting both files and directories (implemented on `IOSDevice3` and `WindowsDevice`; other platforms raise `NotImplementedError`)
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

## Host orchestration (`idevice.host`)

The `host` package drives a memory-measurement run from a **host machine** (mac or Windows). The host never dials the device directly: it talks to the EndlessKeeper control server over HTTP, which in turn proxies the on-device RemoteControlTest runner. A host is always bound to a single keeper and a single target device (`device_udid` / `device_ip`) plus the app `bundle_id` under test.

### Host status

| Host type | Implementation | Notes |
|-----------|----------------|-------|
| `macos` | `MacHost` | Real keeper-backed host |
| `windows` | `WindowsHost` | Real keeper-backed host (HTTP-only, mirrors `MacHost`) |
| anything else (`ios`, `android`, …) | `DummyHost` | No-op host; every operation reports unhealthy and returns an inert default |

### Quick start

Build a host explicitly, or from the controller-injected `GAUTO_*` environment:

```python
from idevice.host import Host

# Explicit
host = Host.create(
    host_type="macos",
    keeper_ip="192.168.0.10",
    device_udid="00000000-0000000000000000",
    device_ip="192.168.0.20",
    bundle_id="com.example.app",
)

# Or from GAUTO_* environment variables (never raises; falls back to DummyHost)
host = Host.from_env()

host.health()                       # keeper reachable?
host.launch_app(timeout=300.0)      # start run, wait for runner, launch app
host.capture_memgraph(timeout=60.0) # open a measured window that auto-closes
summary = host.export()             # keeper presigns + uploads; returns download_url
host.screenshot("shot.png")         # capture one screenshot via the runner proxy
host.tap(0.5, 0.5)                  # tap at normalized screen coordinates
host.kill()                         # tear down the keeper run
```

The most recently built host is also reachable anywhere via `Host.Instance`.

`Host.create` / `Host.from_env` return a `HostBase` exposing: `health()`, `runner()`, `launch_app()`, `capture_memgraph()`, `export()`, `screenshot()`, `tap()`, `status()`, `kill()`, and `exit()`. Errors are raised as `HostError` (with `KeeperError`, `RunnerError`, `HostTimeoutError`, and `HostNotSupportedError` subclasses).

### Lower-level clients

- **`Keeper`** — thin HTTP client for the EndlessKeeper control server (`/api/runs` routes): `launch`, `launch_app`, `status`, `list_runs`, `kill`, `export`, `health`.
- **`Runner`** — thin HTTP client for the on-device runner, reached through the keeper proxy: `launch_app`, `activate`, `terminate`, `start_measuring` / `stop_measuring` / `measuring_status`, `dt_measuring`, `screenshot` (+ periodic), `tap`, `exit`, `health`.

## Configuration

Environment variables override default binary paths:

| Variable | Default | Used by |
|----------|---------|---------|
| `IDEVICE_IOS_BINARY` | `ios` | `IOSDevice` |
| `IDEVICE_IOS3_BINARY` | `/opt/ios3/bin/pymobiledevice3` (Unix) / `~/ios3/bin/pymobiledevice3.exe` (Windows) | `IOSDevice3` |
| `IDEVICE_ADB_BINARY` | `adb` | `AndroidDevice`, `AndroidUIAuto` |
| `IDEVICE_POWERSHELL_BINARY` | `powershell` | `WindowsDevice` |

User data (e.g. installed-app cache) is stored under `~/.idevice` by default.

The `idevice.host` orchestrator reads its configuration from the controller-injected environment (used by `Host.from_env`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `GAUTO_HOST_TYPE` | — | Host type (`macos` / `windows` run a real host; others → `DummyHost`) |
| `GAUTO_HOST_IP` | — | EndlessKeeper control-server IP |
| `GAUTO_HOST_PORT` | `18200` | Keeper control-server port |
| `GAUTO_HOST_ID` | — | Optional keeper/controller id (informational) |
| `GAUTO_DEVICE_UDID` | — | Target device UDID |
| `GAUTO_DEVICE_IP` | — | Target device IP |
| `GAUTO_DEVICE_SERVER_PORT` | `18100` | On-device runner port |
| `GAUTO_PACKAGE_NAME` | — | Target app bundle id |
| `IDEVICE_HOST_TIMEOUT` | `60` | Per-request HTTP timeout (seconds) |
| `IDEVICE_HOST_READY_TIMEOUT` | `300` | Runner readiness timeout (seconds) |

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
