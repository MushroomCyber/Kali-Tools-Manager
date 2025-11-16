"""Kali Tools Manager package."""

from __future__ import annotations

import logging

try:
    from rich.console import Console
except ImportError:  # pragma: no cover - fallback for minimal environments
    class Console:  # type: ignore[override]
        """Very small subset of the Rich Console API used in tests."""

        width = 80

        class _Size:
            width = 80
            height = 24

        @property
        def size(self):
            return self._Size()

        def print(self, *args, **kwargs):  # noqa: D401 - mimic rich API
            print(*args)

        def clear(self):
            pass


__all__ = [
    "console",
    "logger",
    "configure_logging",
]

__version__ = "0.1.0"

console = Console()
logger = logging.getLogger("kalitools")


def configure_logging(level: str = "INFO") -> None:
    """Configure package-wide logging (idempotent)."""
    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(level.upper())
        return
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

try:  # Re-export for callers/tests that expect package-level access
    from .manager import KaliToolsManager  # noqa: E402

    __all__.append("KaliToolsManager")
except Exception:  # pragma: no cover - avoid failing during partial installs
    KaliToolsManager = None  # type: ignore
