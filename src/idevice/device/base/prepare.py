"""Abstract ``Prepare`` interface for pre-build project setup."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Prepare(ABC):
    """Prepare a bound target (e.g. an Xcode project) before build or signing."""

    @abstractmethod
    def prepare(self) -> bool:
        """Apply pre-build preparation steps.

        Returns:
            bool: ``True`` if any change was made, ``False`` if already prepared.
        """
        raise NotImplementedError
