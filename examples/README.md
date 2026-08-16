# Examples

## Android: install an APK

Install a package on a connected device and verify it with `pm list packages`:

```bash
uv run python examples/android_device_install.py \
  --serial "$(adb devices | awk 'NR>2 && $2=="device" {print $1; exit}')" \
  --apk tests/apk/app.apk \
  --package com.example.game
```

Optional: dismiss OEM post-install dialogs after install:

```bash
uv run python examples/android_device_install.py \
  --serial emulator-5554 \
  --apk tests/apk/app.apk \
  --package com.example.game \
  --dismiss-dialogs
```

Minimal Python usage:

```python
from pathlib import Path

from idevice.device.android.device import AndroidDevice

device = AndroidDevice("emulator-5554")
device.install(Path("tests/apk/app.apk"), app_id="com.example.game")
assert device.is_installed("com.example.game")
```

## iOS4: install and launch through ios4

```bash
IDEVICE_IOS4_BINARY=/path/to/ios4 \
uv run python examples/ios4_device.py \
  --udid 00000000-0000000000000000 \
  --ipa path/to/ExampleGame.ipa \
  --app-id com.example.game \
  --malloc-stack-logging \
  --memgraph example-game.memgraph \
  --arg=--mode --arg=debug
```

`--arg` and `--env KEY=VALUE` may be repeated. Their order is preserved when
the game is launched through the `process_control` service. When `--memgraph`
is present, the example captures the returned PID immediately after launch.

The IOS4 backend can also keep the preinstalled iwda2 XCTest Runner alive:

```python
from idevice.device.ios4.device import IOSDevice4

device = IOSDevice4("<udid>", device_ip="<device-ip>")
device.run_iwda2(target_bundle_id="com.example.game")
try:
    # screenshot/tap HTTP requests use <device-ip>:18201
    ...
finally:
    device.stop_iwda2()
```
