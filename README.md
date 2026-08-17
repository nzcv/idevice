# idevice

Cross-platform device automation for end-to-end test workflows: install and manage apps on physical devices, transfer files, and drive UI interactions through a small, platform-agnostic API.

The package ships two complementary APIs:

- **`idevice.device`** — local device automation: talk to a USB/network-attached device through platform CLIs (go-ios, pymobiledevice3, adb) to install apps, transfer files, and drive UI.
- **`idevice.host`** — keeper-backed remote orchestration: drive a memory-measurement run on a host machine that talks to the ikeeper control server and the on-device RemoteControlTest runner over HTTP.

## Platform status

| Platform | Backend | App lifecycle | File transfer | Documents sandbox | Swipe | UI automation |
|----------|---------|---------------|---------------|-------------------|-------|---------------|
| iOS | [go-ios](https://github.com/danielpaulus/go-ios) (`IOSDevice`) | Yes | Yes | — | — | Planned (WDA) |
| iOS | [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (`IOSDevice3`) | Yes | Yes (AFC + app sandbox) | Yes | — | Planned (WDA) |
| Android | adb (`AndroidDevice`) | Yes | Yes | — | Yes | Yes (`AndroidUIAuto`) |
| Windows | PowerShell (`WindowsDevice`) | Yes | — | Yes (local filesystem) | — | Planned |

macOS and HarmonyOS are not implemented yet.

## Requirements

- Python >= 3.9
- Platform CLI tools on `PATH` (or configured via environment variables below):
  - **iOS (go-ios):** `ios`
  - **iOS (pymobiledevice3):** `pymobiledevice3` (default: `/opt/ios3/bin/pymobiledevice3` on Unix, `~/ios3/bin/pymobiledevice3.exe` on Windows)
  - **iOS (ios4):** `ios4` (or set `IDEVICE_IOS4_BINARY`); optionally `ideviceinstaller` for installs (or set `IDEVICE_IDEVICEINSTALLER_BINARY`)
  - **iOS (devicectl):** `xcrun` from Xcode, macOS only (or set `IDEVICE_XCRUN_BINARY`); `ios4` is still needed for `capture_memgraph`
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

# iOS via the Rust ios4 lifecycle backend
device = Device.create(
    "ios4",
    device_id="00000000-0000000000000000",
    device_ip="",
    package_name="com.example.game",
)

# iOS via Apple's own xcrun devicectl (macOS + Xcode only)
device = Device.create(
    "ios5",
    device_id="00000000-0000000000000000",
    device_ip="",
    package_name="com.example.game",
)

# Android via adb (package_name is the default app id for stop_app())
device = Device.create(
    Platform.ANDROID,
    device_id="emulator-5554",
    device_ip="",
    package_name="com.example.app",
)

device.install(Path("MyApp.ipa"), app_id="com.example.app")
device.launch_app("com.example.app")
device.is_installed("com.example.app")
device.stop_app()  # uses bound package_name
device.stop_app("com.example.app")  # explicit override
device.uninstall("com.example.app")
```

Launch an iOS game through `IOSDevice4` with malloc stack logging and ordered
command-line arguments:

```python
from idevice.device.ios4.device import IOSDevice4

game = IOSDevice4(
    "00000000-0000000000000000",
    package_name="com.example.game",
)
game.launch_app(
    "com.example.game",
    args=["--mode", "debug", "--label", "heap capture"],
    environment={"MallocStackLogging": "1"},
)
print(game.last_launch_pid)
snapshot = game.capture_memgraph("trash-dash.memgraph")
print(snapshot)
```

Run the preinstalled `iwda2-Runner` through the IOS4 XCTest client. The
host-side `ios4` process stays alive in the background until
`stop_iwda2()` is called:

```python
from idevice.device.ios4.device import IOSDevice4

device = IOSDevice4(
    "00000000-0000000000000000",
    device_ip="192.168.1.20",  # enables /api/health readiness polling
)
startup_thread = device.run_iwda2(log_path="iwda2.log")
# Startup and /api/health polling run in the background.
print(startup_thread.name)

try:
    # iwda2 is now available at http://192.168.1.20:18200.
    device.tap(0.5, 0.5, app_id="com.example.game")  # GET /api/tap
finally:
    device.stop_iwda2()
```

`tap` takes normalized `[0, 1]` coordinates: `(0, 0)` is the top-left corner
and `(1, 1)` the bottom-right. Pass `app_id` so the offset is anchored to that
app's frame instead of SpringBoard's portrait-locked one — that is what makes
taps land correctly in landscape. It needs a reachable
iwda2 Runner: without `device_ip`, or when the Runner is down, it raises
`IOSDevice4Error`. Note that `screenshot` still goes through the `ios4`
screenshot service, whose framebuffer capture is always portrait, so its axes
only match a tap while the app runs in portrait.

The default Runner bundle ID is `com.idevice.iwda2.xctrunner`, and the Runner
serves HTTP on its own default port (18200) — the port `tap` dials. If
`device_ip` is empty, the startup thread verifies that the local XCTest client
did not exit immediately but skips HTTP readiness polling. Pass
`wait_ready=False` to skip polling even when an IP is configured. Inspect
`device.iwda2_startup_error` after the thread finishes to detect startup errors.

`IOSDevice5` runs the same workflow on Apple's `xcrun devicectl`. The Runner is
started as an ordinary app because the iwda2 bundle self-boots its own
`XCTestConfiguration`, so there is no host-side XCTest client and
`iwda2_process_id` reports the device-side PID:

```python
from idevice.device.ios5.device import IOSDevice5

game = IOSDevice5(
    IOSDevice5.default_udid(),  # first USB-attached device
    device_ip="192.168.1.20",
    package_name="com.example.game",
)
game.run_iwda2().join()  # launches com.idevice.iwda2.xctrunner, polls /api/health
game.launch_app(
    "com.example.game",
    args=["--mode", "debug"],
    environment={"MallocStackLogging": "1"},
)
game.tap(0.5, 0.5, app_id="com.example.game")
game.screenshot("screen.png")
snapshot = game.capture_memgraph("trash-dash.memgraph")  # shells out to ios4
```

`run_iwda2` injects `SERVER_PORT` (default 18201, override with
`IDEVICE_IWDA2_PORT` or the `iwda2_server_port` argument), `MAX_SESSION_SECONDS=0`
and, from the bound `package_name`, `TARGET_BUNDLE_ID`.

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

# iOS (ios4): install and launch a game
uv run python examples/ios4_device.py \
  --udid 00000000-0000000000000000 \
  --ipa path/to/game.ipa \
  --app-id com.example.game \
  --malloc-stack-logging \
  --memgraph game.memgraph \
  --arg=--mode --arg=debug

# iOS (devicectl): install, start iwda2, launch, screenshot
uv run python examples/ios5_device.py \
  --udid 00000000-0000000000000000 \
  --device-ip 192.168.1.20 \
  --ipa path/to/game.ipa \
  --app-id com.example.game \
  --run-iwda2 \
  --screenshot screen.png

# Install an IPA and exercise sandbox file transfer
uv run python examples/ios3_device.py \
  --ipa path/to/app.ipa \
  --app-id com.example.app \
  --sandbox-app-id com.example.app

# Host orchestration: keeper-backed memory-measurement run (see "Host orchestration")
uv run python examples/host_example.py --from-env --bundle-id com.example.app
```

See the module docstrings in each example for prerequisites (Developer Mode, iOS 17+ tunnel, USB debugging, ikeeper reachability, etc.).

## API overview

### `DeviceBase`

Every platform implementation shares the same interface:

- `install(package_path, app_id=None)` — install `.ipa` / `.apk` and optionally record bundle id → file name
- `uninstall(app_id)` / `is_installed(app_id)` / `get_installed_pkg_name(app_id)` — the latter returns an `InstalledAppInfo(app_id, version, path)` or `None`
- `launch_app(app_id=None)` / `stop_app(app_id=None)` — both use the bound `package_name` when `app_id` is omitted
- `package_name` — default app id set at `Device.create` / `Device.from_env` (`GAUTO_PACKAGE_NAME`)
- `push(local, remote, app_id=None, documents_only=False)` / `pull(remote, local, app_id=None, documents_only=True)` — host ↔ device file transfer
- `ls(remote, app_id=None, recursive=False)` — list a remote directory on the device
- `documents_exists(app_id, remote)` / `documents_ls(app_id, remote)` / `documents_push(app_id, local, remote)` / `documents_pull(app_id, remote, local)` / `documents_rm(app_id, remote)` — app Documents sandbox, supporting both files and directories (implemented on `IOSDevice3` and `WindowsDevice`, and all but `documents_rm` on `IOSDevice5`; other platforms raise `NotImplementedError`)
- `swipe(x1, y1, x2, y2, duration_ms=300)` — touch gesture (Android implemented; iOS/Windows raise `NotImplementedError`)
- `tap(x, y, app_id=None)` — tap a normalized point in `[0, 1]` (`IOSDevice4` and `IOSDevice5`, via iwda2)
- `screenshot(local)` — capture the screen to a host file
- `host_is_running()` — whether WebDriverAgent / UIAutomator2 host process is up
- `run_iwda2(...)` — launch an iwda2 XCTest Runner (`IOSDevice4` and `IOSDevice5`)
- `stop_iwda2(graceful=True, timeout=10)` — stop the active iwda2 XCTest Runner (`IOSDevice4` and `IOSDevice5`)
- `capture_memgraph(output, pid=None)` — capture a process memory snapshot (`IOSDevice4`, and `IOSDevice5` by shelling out to `ios4`)

Use `Device.create(Platform, device_id=…, device_ip="", package_name=…)` or
construct `IOSDevice`, `IOSDevice3`, `IOSDevice4`, `IOSDevice5`, `AndroidDevice`,
or `WindowsDevice` directly. `Device.from_env` requires `GAUTO_PACKAGE_NAME` on
all platforms.

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

**`IOSDevice4` (ios4)** — a game lifecycle backend using the Rust
`ios4` binary:

- IPA/app-directory install via the standalone `ideviceinstaller` CLI when present, otherwise `ios4 ideviceinstaller install`
- Exact bundle-id checks via `application_listing`
- Launch via `process_control`, including ordered `argv` and environment values
- Xcode-compatible snapshots via `memgraph`, defaulting to the last launch PID
- Tracks the returned PID so `memgraph` and the runner cleanup can reuse it
- Stop via `pkill --bundle`, which kills the app whether or not this instance launched it
- Screen capture via `screenshot`, and `tap` through the running iwda2 Runner over HTTP
- Does not currently implement file transfer or Documents-sandbox operations

**`IOSDevice5` (xcrun devicectl)** — the same game lifecycle on Apple's own
CoreDevice CLI, so it needs macOS with Xcode but no third-party binary:

- Install/uninstall via `device install app` / `device uninstall app`, caching the bundle id devicectl reports
- Exact bundle-id checks via `device info apps --bundle-id`
- Launch via `device process launch`, with the environment as a JSON dictionary and `argv` as real positional arguments
- Stop by resolving the bundle's processes in `device info processes` and terminating each with `device process terminate --kill`
- App data container transfers via `device copy to` / `device copy from` and listing via `device info files`, including the Documents sandbox
- Screen capture via `device capture screenshot` on Xcode 27+, falling back first to `ios4` and then to the iwda2 Runner's `/api/screenshot`
- `run_iwda2` launches the Runner as a normal app, relying on the iwda2 self-boot library; devicectl has no XCTest command
- `capture_memgraph` shells out to `ios4`: CoreDevice exposes no memory-graph service
- `documents_rm`, `delete2` and `swipe` raise `NotImplementedError` — CoreDevice has no file-removal or touch-injection service

Every command is parsed from devicectl's JSON output, the only interface Apple
guarantees to keep stable, and errors surface as the flattened
`NSLocalizedDescription` chain.

Choose `Platform.IOS`, `Platform.IOS3`, `Platform.IOS4`, or `Platform.IOS5`
depending on which CLI you have deployed. The string values are `ios4` and
`ios5`.

## Host orchestration (`idevice.host`)

The `host` package drives a memory-measurement run from a **host machine** (mac or Windows). The host never dials the device directly: it talks to the ikeeper control server over HTTP, which in turn proxies the on-device RemoteControlTest runner. A host is always bound to a single keeper and a single target device (`device_udid` / `device_ip`) plus the app `bundle_id` under test.

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

# Launch with command-line arguments, applied as XCUIApplication.launchArguments.
# Engine BootConfig values can be overridden this way, since argv beats boot.config:
host.launch_app(args=["-hg-mmap-allocater", "0"])
```

The most recently built host is also reachable anywhere via `Host.Instance`.

`Host.create` / `Host.from_env` return a `HostBase` exposing: `health()`, `runner()`, `launch_app()`, `capture_memgraph()`, `export()`, `screenshot()`, `tap()`, `status()`, `kill()`, and `exit()`. Errors are raised as `HostError` (with `KeeperError`, `RunnerError`, `HostTimeoutError`, and `HostNotSupportedError` subclasses).

### Lower-level clients

- **`Keeper`** — thin HTTP client for the ikeeper control server (`/api/runs` routes): `launch`, `launch_app`, `status`, `list_runs`, `kill`, `export`, `health`.
- **`Runner`** — thin HTTP client for the on-device runner, reached through the keeper proxy: `launch_app`, `activate`, `terminate`, `start_measuring` / `stop_measuring` / `measuring_status`, `dt_measuring`, `screenshot`, `tap`, `exit`, `health`.

## Configuration

Environment variables override default binary paths:

| Variable | Default | Used by |
|----------|---------|---------|
| `IDEVICE_IOS_BINARY` | `ios` | `IOSDevice` |
| `IDEVICE_IOS3_BINARY` | `/opt/ios3/bin/pymobiledevice3` (Unix) / `~/ios3/bin/pymobiledevice3.exe` (Windows) | `IOSDevice3` |
| `IDEVICE_IOS4_BINARY` | `ios4` (`ios4.exe` on Windows) | `IOSDevice4`, `IOSDevice5.capture_memgraph` |
| `IDEVICE_IDEVICEINSTALLER_BINARY` | `ideviceinstaller` (`ideviceinstaller.exe` on Windows) | `IOSDevice4.install` (falls back to `ios4` when missing) |
| `IDEVICE_XCRUN_BINARY` | `xcrun` | `IOSDevice5` |
| `IDEVICE_IWDA2_PORT` | `18201` | `IOSDevice5` iwda2 HTTP port |
| `IDEVICE_ADB_BINARY` | `adb` | `AndroidDevice`, `AndroidUIAuto` |
| `IDEVICE_POWERSHELL_BINARY` | `powershell` | `WindowsDevice` |

User data (e.g. installed-app cache) is stored under `~/.idevice` by default.

The `idevice.host` orchestrator reads its configuration from the controller-injected environment (used by `Host.from_env`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `GAUTO_HOST_TYPE` | — | Host type (`macos` / `windows` run a real host; others → `DummyHost`) |
| `GAUTO_HOST_IP` | — | ikeeper control-server IP |
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
