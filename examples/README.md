# Examples

## Android: install an APK

Install a package on a connected device and verify it with `pm list packages`:

```bash
uv run python examples/android_device_install.py \
  --serial "$(adb devices | awk 'NR>2 && $2=="device" {print $1; exit}')" \
  --apk tests/apk/app.apk \
  --package com.Unity.TrashDash
```

Optional: dismiss OEM post-install dialogs after install:

```bash
uv run python examples/android_device_install.py \
  --serial emulator-5554 \
  --apk tests/apk/app.apk \
  --package com.Unity.TrashDash \
  --dismiss-dialogs
```

Minimal Python usage:

```python
from pathlib import Path

from idevice.device.android.device import AndroidDevice

device = AndroidDevice("emulator-5554")
device.install(Path("tests/apk/app.apk"), app_id="com.Unity.TrashDash")
assert device.is_installed("com.Unity.TrashDash")
```
