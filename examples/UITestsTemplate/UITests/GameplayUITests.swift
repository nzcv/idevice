import XCTest

/// Standalone UI tests that drive an *already installed* app by its bundle id.
///
/// This bundle does **not** depend on the app's Xcode project, so the fixed
/// test cases survive frequent re-exports of the app (e.g. Unity iOS exports).
/// Pipeline: `prepare -> build -> install -> test`.
///
/// Set the target app's bundle id one of two ways:
///   1. Edit `defaultBundleID` below, or
///   2. Pass `APP_BUNDLE_ID` in the scheme's Test action environment.
final class GameplayUITests: XCTestCase {

    /// Replace with your app's bundle id, or override via `APP_BUNDLE_ID`.
    private let defaultBundleID = "com.example.app"

    private var bundleID: String {
        ProcessInfo.processInfo.environment["APP_BUNDLE_ID"] ?? defaultBundleID
    }

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    /// Smoke test: the installed app launches and reaches the foreground.
    func testAppLaunches() throws {
        let app = XCUIApplication(bundleIdentifier: bundleID)
        app.launch()
        XCTAssertTrue(
            app.wait(for: .runningForeground, timeout: 15),
            "App \(bundleID) did not reach the foreground"
        )
    }

    /// Example flow. Replace the interactions with your real scenario.
    func testHappyPath() throws {
        let app = XCUIApplication(bundleIdentifier: bundleID)
        app.launch()

        // TODO: replace with real interactions, e.g.:
        // app.buttons["Start"].tap()
        // XCTAssertTrue(app.staticTexts["Score"].waitForExistence(timeout: 10))

        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 15))
    }
}
