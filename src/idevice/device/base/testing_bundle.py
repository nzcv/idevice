"""Abstract ``UpsertTestingBundle`` interface for XCTest scaffolding."""

from __future__ import annotations

from abc import ABC, abstractmethod


class UpsertTestingBundle(ABC):
    """Create or update a UI Testing Bundle target in a bound Xcode project.

    Implementations automate the manual Xcode steps required to run
    ``XCTMemoryMetric`` performance tests:

    1. Add a UI Testing Bundle target to ``project.pbxproj``.
    2. Write the Swift performance test source.
    3. Wire the test into a shared ``.xcscheme`` Test action.
    """

    @abstractmethod
    def upsert_testing_bundle(
        self,
        *,
        bundle_name: str = "UnityMemoryUITests",
        test_class: str = "UnityMemoryUITests",
        test_method: str = "testGameplayMemory",
        app_target: str | None = None,
        scheme: str | None = None,
    ) -> bool:
        """Create or update the UI Testing Bundle target and scheme.

        Args:
            bundle_name: Name of the UI Testing Bundle target (e.g.
                ``UnityMemoryUITests``).
            test_class: Swift ``XCTestCase`` subclass name.
            test_method: Performance test method name.
            app_target: Name of the main app target under test. When omitted,
                a default is inferred from the project.
            scheme: Scheme to wire the test into. Defaults to ``app_target``.

        Returns:
            bool: ``True`` if any file was created or modified, ``False`` if the
            bundle, source, and scheme were already present.

        Raises:
            RuntimeError: If the project cannot be resolved or patched.
        """
        raise NotImplementedError
