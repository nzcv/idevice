"""Shared subprocess runner for device CLI tools."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass

from idevice.device.base.errors import CommandExecutionError

logger = logging.getLogger(__name__)

DEFAULT_COMMAND_TIMEOUT = 5 * 60  # 5 minutes
_OUTPUT_LIMIT = 500


def _clip_output(value: str | bytes | None) -> str:
    """Return a short single-block view of subprocess output."""
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    text = text.strip()
    if len(text) <= _OUTPUT_LIMIT:
        return text
    return text[:_OUTPUT_LIMIT] + "..."


@dataclass(frozen=True)
class CommandResult:
    """Result of a subprocess command."""

    returncode: int
    stdout: str
    stderr: str


class SubprocessRunner:
    """Execute external CLI commands with consistent error handling."""

    def __init__(self, timeout: int | None = None) -> None:
        env_timeout = os.environ.get("IDEVICE_COMMAND_TIMEOUT")
        if timeout is not None:
            self.timeout = timeout
        elif env_timeout:
            self.timeout = int(env_timeout)
        else:
            self.timeout = DEFAULT_COMMAND_TIMEOUT

    def run(
        self,
        command: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        """Run a command and optionally raise on non-zero exit."""

        # commond join with spaces
        command_str = " ".join(command)
        logger.info(f"Running command: {command_str}")
        effective_timeout = timeout or self.timeout
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                input=input_text,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = round(time.monotonic() - started, 3)
            stdout = _clip_output(exc.stdout)
            stderr = _clip_output(exc.stderr)
            message = (
                f"Command timed out after {effective_timeout}s "
                f"(elapsed {elapsed}s, call_timeout={timeout}, "
                f"default_timeout={self.timeout}): {command_str}"
            )
            if stderr:
                message = f"{message}; stderr={stderr!r}"
            logger.warning(
                "%s stdout=%r",
                message,
                stdout,
            )
            raise CommandExecutionError(
                message,
                command=command,
                stdout=stdout,
                stderr=stderr,
            ) from exc
        except FileNotFoundError as exc:
            logger.warning(f"Command not found: {command[0]}")
            raise CommandExecutionError(
                f"Command not found: {command[0]}",
                command=command,
            ) from exc

        result = CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and completed.returncode != 0:
            logger.warning(f"Command failed with exit code {completed.returncode}: {' '.join(command)}")
            raise CommandExecutionError(
                f"Command failed with exit code {completed.returncode}: {' '.join(command)}",
                command=command,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        return result
