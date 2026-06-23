# Examples

## iOS (Xcode): standalone XCUITest project template

[`UITestsTemplate/`](UITestsTemplate/) is a fixed, version-controlled UI test
project that is fully decoupled from the (frequently regenerated) app project.
Tests launch the installed app by bundle id, so the app project can be
re-exported freely without ever touching the tests. Pipeline:
`prepare -> build -> install -> test`.

```python
from idevice.device import XCDevice

xc = XCDevice("/path/to/Unity-iPhone")
xc.prepare()
xc.build()  # then install the .app on the device

xc.test(
    test_project="examples/UITestsTemplate/UITestsTemplate.xcodeproj",
    scheme="UITests",
    udid="<UDID>",
)
```

See [`UITestsTemplate/README.md`](UITestsTemplate/README.md) for how to set the
app bundle id, signing team, and add test cases. A full Python walkthrough is in
[`xc_device.py`](xc_device.py).

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
  --serial e8b2b043 \
  --apk tests/apk/app.apk \
  --package com.Unity.TrashDash \
  --dismiss-dialogs
```

Minimal Python usage:

```python
from pathlib import Path

from idevice.device.android.device import AndroidDevice

device = AndroidDevice("e8b2b043")
device.install(Path("tests/apk/app.apk"), app_id="com.Unity.TrashDash")
assert device.is_installed("com.Unity.TrashDash")
```
