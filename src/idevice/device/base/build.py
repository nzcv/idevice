"""Abstract ``Build`` interface for compiling bound targets."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Build(ABC):
    """Build a bound target (e.g. an Xcode project) into an installable artifact."""

    @abstractmethod
    def build(
        self,
        *,
        configuration: str = "Release",
        scheme: str | None = None,
        output_dir: Path | str | None = None,
    ) -> Path:
        """Build the bound target.

        Args:
            configuration: Build configuration (e.g. ``Release`` or ``Debug``).
            scheme: Optional Xcode scheme name. When omitted, a default is inferred
                from the project.
            output_dir: Optional directory for build artifacts. When omitted, a
                ``build/`` directory under the project root is used.

        Returns:
            Path: Path to the built ``.app`` bundle.

        Raises:
            RuntimeError: If the build fails or the artifact cannot be located.
        """
        raise NotImplementedError
