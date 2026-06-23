# Standalone XCUITest Project Template (方案一)

A fixed, version-controlled UI test project that is **fully decoupled** from the
app's Xcode project. The app project can be regenerated as often as you like
(e.g. a Unity iOS export) and this test project never needs to change, because
the tests launch the *installed* app by its bundle id:

```swift
let app = XCUIApplication(bundleIdentifier: "com.example.app")
app.launch()
```

## Layout

```
UITestsTemplate/
├── UITestsTemplate.xcodeproj/      # standalone project (no host app)
│   ├── project.pbxproj
│   └── xcshareddata/xcschemes/
│       └── UITests.xcscheme        # shared scheme: `-scheme UITests`
└── UITests/
    └── GameplayUITests.swift       # your fixed test cases
```

The single target is a **UI Testing Bundle** with no "target application", so it
can drive any installed app via `XCUIApplication(bundleIdentifier:)`.

## Customize

1. Set your app's bundle id: edit `defaultBundleID` in
   `UITests/GameplayUITests.swift`, or pass `APP_BUNDLE_ID` in the scheme's Test
   action environment.
2. Set a signing team: open the project in Xcode and pick a Team, or set
   `DEVELOPMENT_TEAM` in `project.pbxproj` (required to run on a physical device).
3. Add your own `*.swift` test files under `UITests/` and to the target's
   Sources build phase (add them in Xcode, then commit the project).

## Run via idevice

The whole pipeline is `prepare -> build -> install -> test`:

```python
from pathlib import Path

from idevice.device import Device, XCDevice

# 1) Prepare + build the (frequently regenerated) app project.
xc = XCDevice("/path/to/Unity-iPhone")
xc.prepare()
app_path = xc.build()

# 2) Install app_path on the device (IOSDevice / IOSDevice3), e.g.:
ios = Device.create("ios3", device_id="<UDID>", device_ip="")
ios.install(Path(app_path), app_id="com.example.app")

# 3) Run this fixed test project against the installed app.
result = xc.test(
    test_project="examples/UITestsTemplate/UITestsTemplate.xcodeproj",
    scheme="UITests",
    udid="<UDID>",
    # only_testing=["UITests/GameplayUITests/testHappyPath"],
)
print(result)  # path to the .xcresult bundle
```

## Run directly with xcodebuild

```bash
xcodebuild test \
  -project examples/UITestsTemplate/UITestsTemplate.xcodeproj \
  -scheme UITests \
  -destination 'platform=iOS,id=<UDID>' \
  -resultBundlePath build/TestResults.xcresult
```
