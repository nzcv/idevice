"""Abstract ``Test`` interface for running a standalone XCTest project."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Test(ABC):
    """Run a standalone XCTest / XCUITest project against a target device.

    The test project is kept separate from any frequently regenerated app
    project (e.g. a Unity iOS export). XCUITest cases launch the already
    installed app by bundle identifier::

        let app = XCUIApplication(bundleIdentifier: "com.example.app")
        app.launch()

    so the fixed test project never needs to be re-integrated when the app
    project is regenerated. The typical pipeline is
    ``prepare -> build -> install -> test``.
    """

    @abstractmethod
    def test(
        self,
        *,
        test_project: Path | str,
        scheme: str,
        udid: str | None = None,
        destination: str | None = None,
        configuration: str = "Debug",
        result_bundle_path: Path | str | None = None,
        output_dir: Path | str | None = None,
        only_testing: list[str] | None = None,
    ) -> Path:
        """Run the standalone test project against a device.

        Args:
            test_project: Path to the fixed, version-controlled ``.xcodeproj``
                or ``.xcworkspace`` that holds the XCTest / XCUITest cases.
            scheme: Xcode scheme that contains the test action to run.
            udid: UDID of the target physical device. Used to build a default
                ``platform=iOS,id=<udid>`` destination when ``destination`` is
                omitted.
            destination: Explicit ``xcodebuild`` destination specifier. Takes
                precedence over ``udid`` when provided.
            configuration: Build configuration for the test action (usually
                ``Debug``).
            result_bundle_path: Optional path for the ``.xcresult`` bundle. When
                omitted, a default under ``output_dir`` is used.
            output_dir: Optional directory for build artifacts (DerivedData and
                the default result bundle). Defaults to a ``build/`` directory
                next to the test project.
            only_testing: Optional list of test identifiers to restrict the run
                (e.g. ``["UITests/LoginTests/testHappyPath"]``).

        Returns:
            Path: Path to the generated ``.xcresult`` bundle.

        Raises:
            RuntimeError: If the test run fails or the project cannot be resolved.
        """
        raise NotImplementedError
