"""Stdlib builders for inserting a UI Testing Bundle into an Xcode project.

These helpers are pure string transforms (no file IO) so they can be unit
tested in isolation. :class:`~idevice.device.xc.device.XCDevice` owns project
resolution, file IO, and error wrapping.

The functions cover the three manual Xcode steps:

1. :func:`insert_ui_test_target` - add a ``PBXNativeTarget`` (and its supporting
   objects) to ``project.pbxproj``.
2. :func:`render_test_source` - render the ``XCTMemoryMetric`` Swift test.
3. :func:`render_scheme` / :func:`add_testable_to_scheme` - create or patch a
   shared ``.xcscheme`` Test action.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET

# Roles for which a deterministic pbxproj object id is generated.
_ROLES = (
    "target",
    "product",
    "src_ref",
    "build_file",
    "group",
    "sources",
    "frameworks",
    "resources",
    "config_debug",
    "config_release",
    "config_list",
    "dependency",
    "proxy",
)

_NATIVE_TARGET_RE = re.compile(
    r"([0-9A-F]{24}) /\* (?P<name>[^*]+?) \*/ = \{\s*isa = PBXNativeTarget;(?P<body>[^{}]*?)\};",
)
_PRODUCT_TYPE_RE = re.compile(r"productType = \"?(?P<type>[^\";]+)\"?;")


def generate_object_id(bundle_name: str, role: str) -> str:
    """Return a stable 24-char uppercase hex id for an object ``role``.

    Deterministic so repeated runs and tests produce identical ids.
    """
    digest = hashlib.sha1(f"idevice-xctest:{bundle_name}:{role}".encode()).hexdigest()
    return digest[:24].upper()


def render_test_source(test_class: str, test_method: str) -> str:
    """Render the ``XCTMemoryMetric`` performance test (doc step 2)."""
    return f"""import XCTest

final class {test_class}: XCTestCase {{

    func {test_method}() throws {{
        let app = XCUIApplication()
        let options = XCTMeasureOptions()
        options.invocationOptions = [.manuallyStart]

        measure(
            metrics: [XCTMemoryMetric(application: app)],
            options: options
        ) {{
            app.launch()

            // Wait for the game to become ready (see HTTP / Accessibility docs).
            // try waitUntilGameReady(timeout: 60)

            startMeasuring()

            // Perform the memory-sensitive operations here.
            // e.g. enter battle, switch UI, reload a level.
            sleep(30)

            XCTAssertTrue(app.state == .runningForeground)
        }}
    }}
}}
"""


def find_native_targets(pbxproj: str) -> list[tuple[str, str, str]]:
    """Return ``(id, name, product_type)`` for each ``PBXNativeTarget``."""
    targets: list[tuple[str, str, str]] = []
    for match in _NATIVE_TARGET_RE.finditer(pbxproj):
        target_id = match.group(1)
        name = match.group("name").strip().strip('"')
        type_match = _PRODUCT_TYPE_RE.search(match.group("body"))
        product_type = type_match.group("type") if type_match else ""
        targets.append((target_id, name, product_type))
    return targets


def resolve_app_target(pbxproj: str, app_target: str | None) -> tuple[str, str]:
    """Resolve the main app target ``(id, name)`` to attach the test to.

    Args:
        pbxproj: Contents of ``project.pbxproj``.
        app_target: Explicit target name, or ``None`` to infer.

    Raises:
        ValueError: If no suitable application target is found.
    """
    targets = find_native_targets(pbxproj)
    if not targets:
        raise ValueError("No PBXNativeTarget found in project.pbxproj")

    if app_target:
        for target_id, name, _ in targets:
            if name == app_target:
                return target_id, name
        raise ValueError(f"App target {app_target!r} not found in project.pbxproj")

    for target_id, name, product_type in targets:
        if product_type == "com.apple.product-type.application":
            return target_id, name
    return targets[0][0], targets[0][1]


def has_ui_test_target(pbxproj: str, bundle_name: str) -> bool:
    """Return ``True`` if a target named ``bundle_name`` already exists."""
    return any(name == bundle_name for _, name, _ in find_native_targets(pbxproj))


def _resolve_project_object_id(pbxproj: str) -> str:
    match = re.search(r"rootObject = ([0-9A-F]{24})", pbxproj)
    if not match:
        raise ValueError("rootObject not found in project.pbxproj")
    return match.group(1)


def _build_objects_block(
    ids: dict[str, str],
    *,
    bundle_name: str,
    test_class: str,
    app_target_id: str,
    app_target_name: str,
    project_id: str,
) -> str:
    bundle_id = f"com.idevice.{re.sub(r'[^A-Za-z0-9]', '', bundle_name)}"
    common_settings = (
        "\t\t\t\tCODE_SIGN_STYLE = Automatic;\n"
        "\t\t\t\tCURRENT_PROJECT_VERSION = 1;\n"
        "\t\t\t\tGENERATE_INFOPLIST_FILE = YES;\n"
        "\t\t\t\tMARKETING_VERSION = 1.0;\n"
        f'\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = "{bundle_id}";\n'
        '\t\t\t\tPRODUCT_NAME = "$(TARGET_NAME)";\n'
        "\t\t\t\tSDKROOT = iphoneos;\n"
        "\t\t\t\tSWIFT_EMIT_LOC_STRINGS = NO;\n"
        "\t\t\t\tSWIFT_VERSION = 5.0;\n"
        '\t\t\t\tTARGETED_DEVICE_FAMILY = "1,2";\n'
        f'\t\t\t\tTEST_TARGET_NAME = "{app_target_name}";\n'
    )

    def config_block(config_id: str, name: str) -> str:
        return (
            f"\t\t{config_id} /* {name} */ = {{\n"
            "\t\t\tisa = XCBuildConfiguration;\n"
            "\t\t\tbuildSettings = {\n"
            f"{common_settings}"
            "\t\t\t};\n"
            f"\t\t\tname = {name};\n"
            "\t\t};\n"
        )

    config_list_comment = f'Build configuration list for PBXNativeTarget "{bundle_name}"'
    return (
        f"\t\t{ids['build_file']} /* {test_class}.swift in Sources */ = {{\n"
        "\t\t\tisa = PBXBuildFile;\n"
        f"\t\t\tfileRef = {ids['src_ref']} /* {test_class}.swift */;\n"
        "\t\t};\n"
        f"\t\t{ids['proxy']} /* PBXContainerItemProxy */ = {{\n"
        "\t\t\tisa = PBXContainerItemProxy;\n"
        f"\t\t\tcontainerPortal = {project_id} /* Project object */;\n"
        "\t\t\tproxyType = 1;\n"
        f"\t\t\tremoteGlobalIDString = {app_target_id};\n"
        f'\t\t\tremoteInfo = "{app_target_name}";\n'
        "\t\t};\n"
        f"\t\t{ids['product']} /* {bundle_name}.xctest */ = {{\n"
        "\t\t\tisa = PBXFileReference;\n"
        "\t\t\texplicitFileType = wrapper.cfbundle;\n"
        "\t\t\tincludeInIndex = 0;\n"
        f'\t\t\tpath = "{bundle_name}.xctest";\n'
        "\t\t\tsourceTree = BUILT_PRODUCTS_DIR;\n"
        "\t\t};\n"
        f"\t\t{ids['src_ref']} /* {test_class}.swift */ = {{\n"
        "\t\t\tisa = PBXFileReference;\n"
        "\t\t\tlastKnownFileType = sourcecode.swift;\n"
        f'\t\t\tpath = "{test_class}.swift";\n'
        '\t\t\tsourceTree = "<group>";\n'
        "\t\t};\n"
        f"\t\t{ids['group']} /* {bundle_name} */ = {{\n"
        "\t\t\tisa = PBXGroup;\n"
        "\t\t\tchildren = (\n"
        f"\t\t\t\t{ids['src_ref']} /* {test_class}.swift */,\n"
        "\t\t\t);\n"
        f'\t\t\tpath = "{bundle_name}";\n'
        '\t\t\tsourceTree = "<group>";\n'
        "\t\t};\n"
        f"\t\t{ids['sources']} /* Sources */ = {{\n"
        "\t\t\tisa = PBXSourcesBuildPhase;\n"
        "\t\t\tbuildActionMask = 2147483647;\n"
        "\t\t\tfiles = (\n"
        f"\t\t\t\t{ids['build_file']} /* {test_class}.swift in Sources */,\n"
        "\t\t\t);\n"
        "\t\t\trunOnlyForDeploymentPostprocessing = 0;\n"
        "\t\t};\n"
        f"\t\t{ids['frameworks']} /* Frameworks */ = {{\n"
        "\t\t\tisa = PBXFrameworksBuildPhase;\n"
        "\t\t\tbuildActionMask = 2147483647;\n"
        "\t\t\tfiles = (\n"
        "\t\t\t);\n"
        "\t\t\trunOnlyForDeploymentPostprocessing = 0;\n"
        "\t\t};\n"
        f"\t\t{ids['resources']} /* Resources */ = {{\n"
        "\t\t\tisa = PBXResourcesBuildPhase;\n"
        "\t\t\tbuildActionMask = 2147483647;\n"
        "\t\t\tfiles = (\n"
        "\t\t\t);\n"
        "\t\t\trunOnlyForDeploymentPostprocessing = 0;\n"
        "\t\t};\n"
        f"{config_block(ids['config_debug'], 'Debug')}"
        f"{config_block(ids['config_release'], 'Release')}"
        f"\t\t{ids['config_list']} /* {config_list_comment} */ = {{\n"
        "\t\t\tisa = XCConfigurationList;\n"
        "\t\t\tbuildConfigurations = (\n"
        f"\t\t\t\t{ids['config_debug']} /* Debug */,\n"
        f"\t\t\t\t{ids['config_release']} /* Release */,\n"
        "\t\t\t);\n"
        "\t\t\tdefaultConfigurationIsVisible = 0;\n"
        "\t\t\tdefaultConfigurationName = Release;\n"
        "\t\t};\n"
        f"\t\t{ids['dependency']} /* PBXTargetDependency */ = {{\n"
        "\t\t\tisa = PBXTargetDependency;\n"
        f"\t\t\ttarget = {app_target_id} /* {app_target_name} */;\n"
        f"\t\t\ttargetProxy = {ids['proxy']} /* PBXContainerItemProxy */;\n"
        "\t\t};\n"
        f"\t\t{ids['target']} /* {bundle_name} */ = {{\n"
        "\t\t\tisa = PBXNativeTarget;\n"
        f"\t\t\tbuildConfigurationList = {ids['config_list']} /* {config_list_comment} */;\n"
        "\t\t\tbuildPhases = (\n"
        f"\t\t\t\t{ids['sources']} /* Sources */,\n"
        f"\t\t\t\t{ids['frameworks']} /* Frameworks */,\n"
        f"\t\t\t\t{ids['resources']} /* Resources */,\n"
        "\t\t\t);\n"
        "\t\t\tbuildRules = (\n"
        "\t\t\t);\n"
        "\t\t\tdependencies = (\n"
        f"\t\t\t\t{ids['dependency']} /* PBXTargetDependency */,\n"
        "\t\t\t);\n"
        f'\t\t\tname = "{bundle_name}";\n'
        f'\t\t\tproductName = "{bundle_name}";\n'
        f"\t\t\tproductReference = {ids['product']} /* {bundle_name}.xctest */;\n"
        '\t\t\tproductType = "com.apple.product-type.bundle.ui-testing";\n'
        "\t\t};\n"
    )


def insert_ui_test_target(
    pbxproj: str,
    *,
    bundle_name: str,
    test_class: str,
    app_target_id: str,
    app_target_name: str,
) -> tuple[str, str]:
    """Insert a UI Testing Bundle target into ``project.pbxproj``.

    Args:
        pbxproj: Current ``project.pbxproj`` contents.
        bundle_name: UI Testing Bundle target name.
        test_class: Swift test class (source file stem).
        app_target_id: pbxproj id of the app target under test.
        app_target_name: Name of the app target under test.

    Returns:
        tuple[str, str]: ``(updated_pbxproj, test_target_id)``.

    Raises:
        ValueError: If required pbxproj anchors are missing.
    """
    project_id = _resolve_project_object_id(pbxproj)
    ids = {role: generate_object_id(bundle_name, role) for role in _ROLES}
    objects_block = _build_objects_block(
        ids,
        bundle_name=bundle_name,
        test_class=test_class,
        app_target_id=app_target_id,
        app_target_name=app_target_name,
        project_id=project_id,
    )

    updated, count = re.subn(
        r"objects = \{\n",
        lambda m: m.group(0) + objects_block,
        pbxproj,
        count=1,
    )
    if count != 1:
        raise ValueError("`objects = {` anchor not found in project.pbxproj")

    target_entry = f"\t\t\t\t{ids['target']} /* {bundle_name} */,\n"
    updated, count = re.subn(
        r"targets = \(\n",
        lambda m: m.group(0) + target_entry,
        updated,
        count=1,
    )
    if count != 1:
        raise ValueError("`targets = (` anchor not found in project.pbxproj")

    product_entry = f"\t\t\t\t{ids['product']} /* {bundle_name}.xctest */,\n"
    updated, count = re.subn(
        r"/\* Products \*/ = \{[^{}]*?children = \(\n",
        lambda m: m.group(0) + product_entry,
        updated,
        count=1,
    )
    if count != 1:
        raise ValueError("Products group not found in project.pbxproj")

    attributes_entry = (
        f"\t\t\t\t{ids['target']} = {{\n"
        "\t\t\t\t\tCreatedOnToolsVersion = 15.0;\n"
        f"\t\t\t\t\tTestTargetID = {app_target_id};\n"
        "\t\t\t\t};\n"
    )
    updated = re.sub(
        r"TargetAttributes = \{\n",
        lambda m: m.group(0) + attributes_entry,
        updated,
        count=1,
    )

    return updated, ids["target"]


def render_scheme(
    *,
    app_target_id: str,
    app_target_name: str,
    test_target_id: str,
    bundle_name: str,
    xcodeproj_name: str,
) -> str:
    """Render a complete shared ``.xcscheme`` building the app and testing the bundle."""
    app_ref = (
        '            <BuildableReference\n'
        '               BuildableIdentifier = "primary"\n'
        f'               BlueprintIdentifier = "{app_target_id}"\n'
        f'               BuildableName = "{app_target_name}.app"\n'
        f'               BlueprintName = "{app_target_name}"\n'
        f'               ReferencedContainer = "container:{xcodeproj_name}">\n'
        '            </BuildableReference>\n'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Scheme\n"
        '   LastUpgradeVersion = "1500"\n'
        '   version = "1.7">\n'
        "   <BuildAction\n"
        '      parallelizeBuildables = "YES"\n'
        '      buildImplicitDependencies = "YES">\n'
        "      <BuildActionEntries>\n"
        "         <BuildActionEntry\n"
        '            buildForTesting = "YES"\n'
        '            buildForRunning = "YES"\n'
        '            buildForProfiling = "YES"\n'
        '            buildForArchiving = "YES"\n'
        '            buildForAnalyzing = "YES">\n'
        f"{app_ref}"
        "         </BuildActionEntry>\n"
        "      </BuildActionEntries>\n"
        "   </BuildAction>\n"
        "   <TestAction\n"
        '      buildConfiguration = "Debug"\n'
        '      selectedDebuggerIdentifier = "Xcode.DebuggerFoundation.Debugger.LLDB"\n'
        '      selectedLauncherIdentifier = "Xcode.DebuggerFoundation.Launcher.LLDB"\n'
        '      shouldUseLaunchSchemeArgsEnv = "YES">\n'
        "      <Testables>\n"
        "         <TestableReference\n"
        '            skipped = "NO">\n'
        '            <BuildableReference\n'
        '               BuildableIdentifier = "primary"\n'
        f'               BlueprintIdentifier = "{test_target_id}"\n'
        f'               BuildableName = "{bundle_name}.xctest"\n'
        f'               BlueprintName = "{bundle_name}"\n'
        f'               ReferencedContainer = "container:{xcodeproj_name}">\n'
        "            </BuildableReference>\n"
        "         </TestableReference>\n"
        "      </Testables>\n"
        "   </TestAction>\n"
        "   <LaunchAction\n"
        '      buildConfiguration = "Debug"\n'
        '      selectedDebuggerIdentifier = "Xcode.DebuggerFoundation.Debugger.LLDB"\n'
        '      selectedLauncherIdentifier = "Xcode.DebuggerFoundation.Launcher.LLDB"\n'
        '      launchStyle = "0"\n'
        '      useCustomWorkingDirectory = "NO"\n'
        '      ignoresPersistentStateOnLaunch = "NO"\n'
        '      debugDocumentVersioning = "YES"\n'
        '      debugServiceExtension = "internal"\n'
        '      allowLocationSimulation = "YES">\n'
        "      <BuildableProductRunnable\n"
        '         runnableDebuggingMode = "0">\n'
        f"{app_ref}"
        "      </BuildableProductRunnable>\n"
        "   </LaunchAction>\n"
        "   <ProfileAction\n"
        '      buildConfiguration = "Release"\n'
        '      shouldUseLaunchSchemeArgsEnv = "YES"\n'
        '      savedToolIdentifier = ""\n'
        '      useCustomWorkingDirectory = "NO"\n'
        '      debugDocumentVersioning = "YES">\n'
        "      <BuildableProductRunnable\n"
        '         runnableDebuggingMode = "0">\n'
        f"{app_ref}"
        "      </BuildableProductRunnable>\n"
        "   </ProfileAction>\n"
        "   <AnalyzeAction\n"
        '      buildConfiguration = "Debug">\n'
        "   </AnalyzeAction>\n"
        "   <ArchiveAction\n"
        '      buildConfiguration = "Release"\n'
        '      revealArchiveInOrganizer = "YES">\n'
        "   </ArchiveAction>\n"
        "</Scheme>\n"
    )


def add_testable_to_scheme(
    scheme_xml: str,
    *,
    test_target_id: str,
    bundle_name: str,
    xcodeproj_name: str,
) -> tuple[str, bool]:
    """Add a ``TestableReference`` for the bundle to an existing scheme.

    Args:
        scheme_xml: Existing ``.xcscheme`` XML contents.
        test_target_id: pbxproj id of the test target.
        bundle_name: UI Testing Bundle target name.
        xcodeproj_name: ``.xcodeproj`` bundle name for ``ReferencedContainer``.

    Returns:
        tuple[str, bool]: ``(scheme_xml, changed)``. ``changed`` is ``False`` when
        the testable reference already exists.

    Raises:
        ValueError: If the scheme XML cannot be parsed.
    """
    try:
        root = ET.fromstring(scheme_xml)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid .xcscheme XML: {exc}") from exc

    buildable_name = f"{bundle_name}.xctest"
    for ref in root.iter("BuildableReference"):
        if (
            ref.get("BlueprintIdentifier") == test_target_id
            or ref.get("BuildableName") == buildable_name
        ):
            return scheme_xml, False

    test_action = root.find("TestAction")
    if test_action is None:
        test_action = ET.SubElement(root, "TestAction")
        test_action.set("buildConfiguration", "Debug")
        test_action.set("shouldUseLaunchSchemeArgsEnv", "YES")
    testables = test_action.find("Testables")
    if testables is None:
        testables = ET.SubElement(test_action, "Testables")

    testable_ref = ET.SubElement(testables, "TestableReference")
    testable_ref.set("skipped", "NO")
    buildable_ref = ET.SubElement(testable_ref, "BuildableReference")
    buildable_ref.set("BuildableIdentifier", "primary")
    buildable_ref.set("BlueprintIdentifier", test_target_id)
    buildable_ref.set("BuildableName", buildable_name)
    buildable_ref.set("BlueprintName", bundle_name)
    buildable_ref.set("ReferencedContainer", f"container:{xcodeproj_name}")

    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n', True
