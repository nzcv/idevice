"""Xcode ``Prepare`` and ``Build`` implementation for Unity / iOS export projects."""

from __future__ import annotations

import logging
import plistlib
import re
import shutil
from pathlib import Path

from idevice.device.base.build import Build
from idevice.device.base.errors import CommandExecutionError
from idevice.device.base.prepare import Prepare
from idevice.device.base.runner import SubprocessRunner
from idevice.device.base.test import Test
from idevice.device.base.testing_bundle import UpsertTestingBundle
from idevice.device.config import xcodebuild_binary
from idevice.device.xc import testing_bundle as _tb

logger = logging.getLogger(__name__)

_LOG_TAG = "[XCDevice]"
_BUILD_TIMEOUT_SECONDS = 3600

_IN_APP_PURCHASE_CAPABILITY = re.compile(
    r"^\t*com\.apple\.InAppPurchase\s*=\s*\{[^}]*\};\s*\n?",
    re.MULTILINE,
)
_STOREKIT_LINE = re.compile(r"^.*StoreKit\.framework.*\n?", re.MULTILINE)

_ENTITLEMENTS_IAP_KEYS = (
    "com.apple.developer.in-app-payments",
    "com.apple.developer.storekit.custom-purchase-link",
    "com.apple.developer.storekit.external-link.account",
)


class XCDeviceError(RuntimeError):
    """Raised when an Xcode project operation fails."""


class XCDevice(Prepare, Build, Test, UpsertTestingBundle):
    """Prepare and build an Xcode project exported from Unity or other tools.

    ``device_id`` is the path to an ``.xcodeproj`` bundle or a directory that
    contains one (typical Unity iOS export layout).
    """

    def __init__(self, device_id: str, *, device_ip: str = "") -> None:
        if not device_id or not isinstance(device_id, str):
            raise ValueError("device_id is required and must be a non-empty string")
        self._device_id = device_id
        self._device_ip = device_ip
        self._platform = "xc"
        self._runner = SubprocessRunner()

    @property
    def platform(self) -> str:
        """Platform bound to this instance."""
        return self._platform

    @property
    def device_id(self) -> str:
        """Path to the bound ``.xcodeproj`` or export directory."""
        return self._device_id

    @property
    def device_ip(self) -> str:
        """Unused for Xcode project preparation; kept for factory parity."""
        return self._device_ip

    def prepare(self) -> bool:
        """Remove the In-App Purchase capability from the bound Xcode project.

        Edits ``project.pbxproj`` to drop ``com.apple.InAppPurchase`` and
        ``StoreKit.framework`` references, and strips related entitlement keys
        from ``*.entitlements`` files in the project tree.

        Returns:
            bool: ``True`` if any file was modified, ``False`` if already clean.

        Raises:
            XCDeviceError: If the project path cannot be resolved.
            FileNotFoundError: If ``project.pbxproj`` is missing.
        """
        project_root = self._resolve_project_root()
        pbxproj = self._resolve_pbxproj(project_root)
        logger.info(f"{_LOG_TAG} Preparing Xcode project: {pbxproj}")

        changed = False
        changed |= self._remove_in_app_purchase_from_pbxproj(pbxproj)
        changed |= self._remove_storekit_from_pbxproj(pbxproj)
        for entitlements_path in self._find_entitlements_files(project_root):
            changed |= self._remove_in_app_purchase_from_entitlements(
                entitlements_path,
            )

        if changed:
            logger.info(f"{_LOG_TAG} Removed In-App Purchase from {project_root}")
        else:
            logger.debug(f"{_LOG_TAG} No In-App Purchase capability found in {project_root}")
        return changed

    def build(
        self,
        *,
        configuration: str = "Release",
        scheme: str | None = None,
        output_dir: Path | str | None = None,
    ) -> Path:
        """Build the bound Xcode project for a physical iOS device.

        Runs ``xcodebuild`` with the ``iphoneos`` SDK and returns the path to
        the generated ``.app`` bundle under ``output_dir/DerivedData``.

        Args:
            configuration: Xcode build configuration (``Release`` or ``Debug``).
            scheme: Xcode scheme name. Defaults to the ``.xcodeproj`` stem.
            output_dir: Directory for build artifacts. Defaults to
                ``<project_root>/build``.

        Returns:
            Path: Path to the built ``.app`` bundle.

        Raises:
            XCDeviceError: If ``xcodebuild`` fails or no ``.app`` is produced.
            ValueError: If ``configuration`` is empty.
        """
        if not configuration:
            raise ValueError("configuration is required and must be a non-empty string")

        binary = xcodebuild_binary()
        if shutil.which(binary) is None and not Path(binary).exists():
            logger.error(f"{_LOG_TAG} `{binary}` CLI not found")
            raise XCDeviceError(
                f"`{binary}` CLI not found. Install Xcode command line tools.",
            )

        project_root = self._resolve_project_root()
        xcodeproj = self._resolve_xcodeproj_bundle(project_root)
        resolved_scheme = scheme or xcodeproj.stem
        artifact_root = Path(output_dir).expanduser().resolve() if output_dir else project_root / "build"
        derived_data = artifact_root / "DerivedData"
        derived_data.mkdir(parents=True, exist_ok=True)

        command = [
            binary,
            "-project",
            str(xcodeproj),
            "-scheme",
            resolved_scheme,
            "-configuration",
            configuration,
            "-sdk",
            "iphoneos",
            "-destination",
            "generic/platform=iOS",
            "-derivedDataPath",
            str(derived_data),
            "build",
        ]
        logger.info(
            f"{_LOG_TAG} Building {xcodeproj.name} "
            f"scheme={resolved_scheme} configuration={configuration}",
        )
        try:
            self._runner.run(command, timeout=_BUILD_TIMEOUT_SECONDS)
        except CommandExecutionError as exc:
            raise XCDeviceError(
                f"xcodebuild failed for scheme={resolved_scheme}: {exc.stderr or exc}",
            ) from exc

        app_path = self._find_built_app(derived_data, configuration, resolved_scheme)
        logger.info(f"{_LOG_TAG} Built app bundle: {app_path}")
        return app_path

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
        """Run a standalone XCTest / XCUITest project against a device.

        The test project is kept separate from the (frequently regenerated) app
        project bound to this instance. XCUITest cases launch the already
        installed app by bundle identifier, so this project never needs to be
        re-integrated when the app project is re-exported. Runs
        ``xcodebuild test`` and returns the path to the ``.xcresult`` bundle.

        Args:
            test_project: Path to the fixed ``.xcodeproj`` or ``.xcworkspace``
                that holds the test cases.
            scheme: Xcode scheme that contains the test action to run.
            udid: UDID of the target physical device. Builds a default
                ``platform=iOS,id=<udid>`` destination when ``destination`` is
                omitted.
            destination: Explicit ``xcodebuild`` destination. Takes precedence
                over ``udid``.
            configuration: Build configuration for the test action.
            result_bundle_path: Optional ``.xcresult`` output path. Defaults to
                ``<output_dir>/TestResults.xcresult``.
            output_dir: Optional directory for DerivedData and the default
                result bundle. Defaults to a ``build/`` directory next to the
                test project.
            only_testing: Optional test identifiers to restrict the run.

        Returns:
            Path: Path to the generated ``.xcresult`` bundle.

        Raises:
            XCDeviceError: If ``xcodebuild`` is missing, the project/destination
                cannot be resolved, or the test run fails.
            ValueError: If ``scheme`` is empty.
        """
        if not scheme:
            raise ValueError("scheme is required and must be a non-empty string")

        binary = xcodebuild_binary()
        if shutil.which(binary) is None and not Path(binary).exists():
            logger.error(f"{_LOG_TAG} `{binary}` CLI not found")
            raise XCDeviceError(
                f"`{binary}` CLI not found. Install Xcode command line tools.",
            )

        project_path, project_flag = self._resolve_test_project(test_project)
        resolved_destination = self._resolve_destination(destination, udid)

        artifact_root = (
            Path(output_dir).expanduser().resolve()
            if output_dir
            else project_path.parent / "build"
        )
        derived_data = artifact_root / "DerivedData"
        derived_data.mkdir(parents=True, exist_ok=True)

        if result_bundle_path is not None:
            result_bundle = Path(result_bundle_path).expanduser().resolve()
        else:
            result_bundle = artifact_root / "TestResults.xcresult"
        # xcodebuild refuses to write into an existing result bundle path.
        if result_bundle.exists():
            shutil.rmtree(result_bundle)
        result_bundle.parent.mkdir(parents=True, exist_ok=True)

        command = [
            binary,
            project_flag,
            str(project_path),
            "-scheme",
            scheme,
            "-configuration",
            configuration,
            "-destination",
            resolved_destination,
            "-derivedDataPath",
            str(derived_data),
            "-resultBundlePath",
            str(result_bundle),
        ]
        for identifier in only_testing or []:
            command.append(f"-only-testing:{identifier}")
        command.append("test")

        logger.info(
            f"{_LOG_TAG} Testing {project_path.name} "
            f"scheme={scheme} destination={resolved_destination}",
        )
        try:
            self._runner.run(command, timeout=_BUILD_TIMEOUT_SECONDS)
        except CommandExecutionError as exc:
            raise XCDeviceError(
                f"xcodebuild test failed for scheme={scheme}: {exc.stderr or exc}",
            ) from exc

        logger.info(f"{_LOG_TAG} Test result bundle: {result_bundle}")
        return result_bundle

    @staticmethod
    def _resolve_test_project(test_project: Path | str) -> tuple[Path, str]:
        if not test_project:
            raise ValueError("test_project is required and must be a non-empty string")
        path = Path(test_project).expanduser().resolve()
        if path.suffix == ".xcworkspace":
            flag = "-workspace"
        elif path.suffix == ".xcodeproj":
            flag = "-project"
        else:
            raise XCDeviceError(
                f"test_project must be a .xcodeproj or .xcworkspace: {test_project}",
            )
        if not path.exists():
            raise XCDeviceError(f"Test project not found: {path}")
        return path, flag

    @staticmethod
    def _resolve_destination(destination: str | None, udid: str | None) -> str:
        if destination:
            return destination
        if udid:
            return f"platform=iOS,id={udid}"
        raise XCDeviceError(
            "A test destination is required: pass udid=... or destination=...",
        )

    def upsert_testing_bundle(
        self,
        *,
        bundle_name: str = "UnityMemoryUITests",
        test_class: str = "UnityMemoryUITests",
        test_method: str = "testGameplayMemory",
        app_target: str | None = None,
        scheme: str | None = None,
    ) -> bool:
        """Create or update a UI Testing Bundle for ``XCTMemoryMetric`` tests.

        Automates the three manual Xcode steps:

        1. Add a UI Testing Bundle target to ``project.pbxproj``.
        2. Write the Swift ``XCTMemoryMetric`` performance test.
        3. Wire the test target into a shared ``.xcscheme`` Test action.

        Args:
            bundle_name: UI Testing Bundle target name.
            test_class: Swift ``XCTestCase`` subclass / source file stem.
            test_method: Performance test method name.
            app_target: Main app target under test. Defaults to the inferred app
                target (the ``.xcodeproj`` application target).
            scheme: Scheme to wire the test into. Defaults to ``app_target``.

        Returns:
            bool: ``True`` if any file was created or modified, ``False`` if the
            bundle, source, and scheme were already present.

        Raises:
            XCDeviceError: If the project cannot be resolved or patched.
            FileNotFoundError: If ``project.pbxproj`` is missing.
        """
        project_root = self._resolve_project_root()
        xcodeproj = self._resolve_xcodeproj_bundle(project_root)
        pbxproj = self._resolve_pbxproj(xcodeproj)
        logger.info(f"{_LOG_TAG} Upserting UI test bundle {bundle_name} into {xcodeproj.name}")

        original = pbxproj.read_text(encoding="utf-8")
        try:
            app_target_id, app_target_name = _tb.resolve_app_target(original, app_target)
        except ValueError as exc:
            raise XCDeviceError(str(exc)) from exc
        resolved_scheme = scheme or app_target_name

        test_target_id = _tb.generate_object_id(bundle_name, "target")
        changed = False

        # Step 1: add the UI Testing Bundle target to project.pbxproj.
        if _tb.has_ui_test_target(original, bundle_name):
            logger.debug(f"{_LOG_TAG} UI test target {bundle_name} already present")
        else:
            try:
                updated, test_target_id = _tb.insert_ui_test_target(
                    original,
                    bundle_name=bundle_name,
                    test_class=test_class,
                    app_target_id=app_target_id,
                    app_target_name=app_target_name,
                )
            except ValueError as exc:
                raise XCDeviceError(f"Failed to insert UI test target: {exc}") from exc
            pbxproj.write_text(updated, encoding="utf-8")
            logger.info(f"{_LOG_TAG} Added UI test target {bundle_name} to {pbxproj.name}")
            changed = True

        # Step 2: write the XCTMemoryMetric Swift performance test.
        changed |= self._write_test_source(project_root, bundle_name, test_class, test_method)

        # Step 3: configure the scheme's Test action.
        changed |= self._upsert_scheme(
            xcodeproj,
            scheme=resolved_scheme,
            app_target_id=app_target_id,
            app_target_name=app_target_name,
            test_target_id=test_target_id,
            bundle_name=bundle_name,
        )

        if changed:
            logger.info(f"{_LOG_TAG} UI test bundle {bundle_name} ready in {xcodeproj.name}")
        else:
            logger.debug(f"{_LOG_TAG} UI test bundle {bundle_name} already up to date")
        return changed

    @staticmethod
    def _write_test_source(
        project_root: Path,
        bundle_name: str,
        test_class: str,
        test_method: str,
    ) -> bool:
        source_path = project_root / bundle_name / f"{test_class}.swift"
        contents = _tb.render_test_source(test_class, test_method)
        if source_path.is_file() and source_path.read_text(encoding="utf-8") == contents:
            logger.debug(f"{_LOG_TAG} Test source already up to date: {source_path}")
            return False
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(contents, encoding="utf-8")
        logger.info(f"{_LOG_TAG} Wrote performance test: {source_path}")
        return True

    @staticmethod
    def _upsert_scheme(
        xcodeproj: Path,
        *,
        scheme: str,
        app_target_id: str,
        app_target_name: str,
        test_target_id: str,
        bundle_name: str,
    ) -> bool:
        scheme_path = xcodeproj / "xcshareddata" / "xcschemes" / f"{scheme}.xcscheme"
        if scheme_path.is_file():
            try:
                updated, changed = _tb.add_testable_to_scheme(
                    scheme_path.read_text(encoding="utf-8"),
                    test_target_id=test_target_id,
                    bundle_name=bundle_name,
                    xcodeproj_name=xcodeproj.name,
                )
            except ValueError as exc:
                raise XCDeviceError(f"Failed to patch scheme {scheme_path.name}: {exc}") from exc
            if not changed:
                logger.debug(f"{_LOG_TAG} Scheme {scheme_path.name} already tests {bundle_name}")
                return False
            scheme_path.write_text(updated, encoding="utf-8")
            logger.info(f"{_LOG_TAG} Added {bundle_name} to scheme {scheme_path.name}")
            return True

        scheme_xml = _tb.render_scheme(
            app_target_id=app_target_id,
            app_target_name=app_target_name,
            test_target_id=test_target_id,
            bundle_name=bundle_name,
            xcodeproj_name=xcodeproj.name,
        )
        scheme_path.parent.mkdir(parents=True, exist_ok=True)
        scheme_path.write_text(scheme_xml, encoding="utf-8")
        logger.info(f"{_LOG_TAG} Created shared scheme {scheme_path.name}")
        return True

    def _resolve_xcodeproj_bundle(self, project_root: Path) -> Path:
        path = Path(self._device_id).expanduser().resolve()
        if path.suffix == ".xcodeproj":
            return path
        matches = sorted(project_root.glob("*.xcodeproj"))
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise XCDeviceError(f"No .xcodeproj found under {project_root}")
        names = ", ".join(match.name for match in matches)
        raise XCDeviceError(
            f"Multiple .xcodeproj bundles found under {project_root}: {names}",
        )

    @staticmethod
    def _find_built_app(
        derived_data: Path,
        configuration: str,
        scheme: str,
    ) -> Path:
        products_dir = derived_data / "Build" / "Products" / f"{configuration}-iphoneos"
        apps = sorted(products_dir.glob("*.app"))
        if not apps:
            raise XCDeviceError(f"No .app bundle found under {products_dir}")

        preferred = products_dir / f"{scheme}.app"
        if preferred in apps:
            return preferred
        if len(apps) == 1:
            return apps[0]
        names = ", ".join(app.name for app in apps)
        raise XCDeviceError(
            f"Multiple .app bundles found under {products_dir}: {names}",
        )

    def _resolve_project_root(self) -> Path:
        path = Path(self._device_id).expanduser().resolve()
        if path.suffix == ".xcodeproj":
            return path.parent
        if path.is_dir() and path.name.endswith(".xcodeproj"):
            return path.parent
        if path.is_dir():
            matches = sorted(path.glob("*.xcodeproj"))
            if len(matches) == 1:
                return path
            if not matches:
                raise XCDeviceError(f"No .xcodeproj found under {path}")
            names = ", ".join(match.name for match in matches)
            raise XCDeviceError(
                f"Multiple .xcodeproj bundles found under {path}: {names}",
            )
        raise XCDeviceError(f"Invalid Xcode project path: {self._device_id}")

    @staticmethod
    def _resolve_pbxproj(project_root: Path) -> Path:
        if project_root.suffix == ".xcodeproj":
            pbxproj = project_root / "project.pbxproj"
        else:
            xcodeproj_dirs = sorted(project_root.glob("*.xcodeproj"))
            pbxproj = xcodeproj_dirs[0] / "project.pbxproj"
        if not pbxproj.is_file():
            raise FileNotFoundError(f"project.pbxproj not found: {pbxproj}")
        return pbxproj

    @staticmethod
    def _find_entitlements_files(project_root: Path) -> list[Path]:
        return sorted(project_root.rglob("*.entitlements"))

    @staticmethod
    def _remove_in_app_purchase_from_pbxproj(pbxproj: Path) -> bool:
        original = pbxproj.read_text(encoding="utf-8")
        updated = _IN_APP_PURCHASE_CAPABILITY.sub("", original)
        if updated == original:
            return False
        pbxproj.write_text(updated, encoding="utf-8")
        logger.debug(f"{_LOG_TAG} Removed com.apple.InAppPurchase from {pbxproj}")
        return True

    @staticmethod
    def _remove_storekit_from_pbxproj(pbxproj: Path) -> bool:
        original = pbxproj.read_text(encoding="utf-8")
        updated = _STOREKIT_LINE.sub("", original)
        if updated == original:
            return False
        pbxproj.write_text(updated, encoding="utf-8")
        logger.debug(f"{_LOG_TAG} Removed StoreKit.framework references from {pbxproj}")
        return True

    @staticmethod
    def _remove_in_app_purchase_from_entitlements(path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                data = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException) as exc:
            logger.warning(f"{_LOG_TAG} Skipping entitlements file {path}: {exc}")
            return False

        if not isinstance(data, dict):
            return False

        removed_keys = [key for key in _ENTITLEMENTS_IAP_KEYS if key in data]
        if not removed_keys:
            return False

        for key in removed_keys:
            del data[key]
        with path.open("wb") as handle:
            plistlib.dump(data, handle)
        logger.debug(
            f"{_LOG_TAG} Removed entitlement keys {removed_keys} from {path}",
        )
        return True
