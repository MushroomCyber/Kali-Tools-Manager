"""Command-line interface helpers for Kali Tools Manager."""

from __future__ import annotations

import argparse
import sys
from typing import Iterable, Optional

from . import configure_logging, console

try:
    import termios  # noqa: F401  # parity with UI module
    import tty  # noqa: F401

    TERMIOS_AVAILABLE = True
except ImportError:
    TERMIOS_AVAILABLE = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover and manage Kali Linux tooling from the terminal")
    parser.add_argument(
        "--mode",
        choices=["auto", "rich", "basic"],
        default="auto",
        help="UI mode: auto-detect, force rich interface, or force basic text mode",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (DEBUG, INFO, WARNING, ERROR)",
    )
    parser.add_argument(
        "--discovery-workers",
        type=int,
        default=8,
        help="Concurrent scraper workers when discovering new tools",
    )
    parser.add_argument(
        "--discovery-delay",
        type=float,
        default=0.2,
        help="Delay between HTTP fetches during discovery (seconds)",
    )
    parser.add_argument(
        "--debug-scraper",
        action="store_true",
        help="Emit verbose scraper diagnostics to debug_scraper.txt",
    )
    return parser


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def resolve_ui_mode(requested: str) -> str:
    platform_is_linux = sys.platform.startswith("linux")
    if requested == "rich" and not platform_is_linux:
        console.print("[yellow]Forcing basic mode because the platform is not Linux.[/yellow]")
        return "basic"
    if requested == "auto":
        return "rich" if platform_is_linux and TERMIOS_AVAILABLE else "basic"
    return requested


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    configure_logging(args.log_level)

    if not sys.platform.startswith("linux"):
        console.print("[red]Kali Tools Manager requires Kali Linux or another Debian-based Linux distribution.[/red]")
        console.print("[yellow]Please run inside Kali instead of Windows/macOS environments.[/yellow]")
        raise SystemExit(1)

    try:
        from .manager import KaliToolsManager
        from .ui import ToolsUI

        manager = KaliToolsManager(
            discovery_workers=args.discovery_workers,
            discovery_delay=args.discovery_delay,
            debug_scraper=args.debug_scraper,
        )
        ui_mode = resolve_ui_mode(args.mode)
        ui = ToolsUI(manager, ui_mode=ui_mode)
        ui.run()
    except KeyboardInterrupt:
        console.print("\n\n[yellow]Application terminated by user[/yellow]\n")
    except Exception as exc:  # pragma: no cover - surface fatal diagnostics
        import traceback

        console.print(f"\n[red]Fatal error:[/red] {exc}\n")
        console.print(traceback.format_exc(), style="dim")
        raise SystemExit(1) from exc
