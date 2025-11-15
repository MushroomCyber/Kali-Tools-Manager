"""Core manager logic for Kali Tools CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm
from rich.syntax import Syntax

from . import console, logger
from .config import ConfigManager
from .constants import (
    CATEGORIES,
    CATEGORY_ICONS,
    CATEGORY_NAMES,
    CATEGORY_KEYWORD_HINTS,
    TOOL_DESCRIPTIONS,
    SUBCATEGORY_KEYWORD_HINTS,
    META_CATEGORY_SOURCES,
    CATEGORY_DEFAULT_SUBCATEGORY,
    get_category_display_name,
    get_subcategory_for,
)
from .model import Tool
from .notifications import notifications_ready, send_notification

try:
    from kalitools_lib.scraping import parse_tool_page  # type: ignore
except Exception:
    parse_tool_page = None  # type: ignore

try:
    import psutil  # type: ignore

    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore
    PSUTIL_AVAILABLE = False

try:
    import requests  # type: ignore
    from bs4 import BeautifulSoup  # type: ignore

    WEB_SCRAPING_AVAILABLE = True
except ImportError:
    requests = None  # type: ignore
    BeautifulSoup = None  # type: ignore
    WEB_SCRAPING_AVAILABLE = False


FALLBACK_TOOL_ENTRIES = [
    {"name": "autopsy", "category": "forensics"},
    {"name": "cutycapt", "category": "web"},
    {"name": "dirbuster", "category": "web"},
    {"name": "feroxbuster", "category": "web"},
    {"name": "fern-wifi-cracker", "category": "wireless"},
    {"name": "gophish", "category": "social"},
    {"name": "guymager", "category": "forensics"},
    {"name": "legion", "category": "recon"},
    {"name": "ophcrack", "category": "password"},
    {"name": "ophcrack-cli", "category": "password"},
    {"name": "sqlmap", "category": "database"},
    {"name": "zenmap", "category": "recon"},
]

LEGACY_FALLBACK_NAMES = [
    "autopsy",
    "cutycapt",
    "dirbuster",
    "faraday",
    "fern-wifi-cracker",
    "gophish",
    "guymager",
    "legion",
    "ophcrack",
    "ophcrack-cli",
    "sqlitebrowser",
    "zenmap",
]

FALLBACK_NAME_VARIANTS = [
    {entry['name'].lower() for entry in FALLBACK_TOOL_ENTRIES},
    {name.lower() for name in LEGACY_FALLBACK_NAMES},
]


class KaliToolsManager:
    """Main class for managing Kali Linux tools with enhanced features"""

    def __init__(
        self,
        discovery_workers: int = 8,
        discovery_delay: float = 0.2,
        debug_scraper: bool = False,
    ):
        # Prefer JSON-based tool definitions; if none exist yet,
        # perform a full discovery pass (web + meta-packages), then
        # persist results to JSON for future runs.

        self.discovery_workers = max(2, discovery_workers)
        self.discovery_delay = max(0.0, discovery_delay)
        self.debug_scraper = debug_scraper

        # Load canonical Kali tools index from web cache if present (best-effort)
        self.web_index: Optional[Dict[str, Any]] = self._load_web_index()
        # Initialize caches that discovery helpers might touch
        self._installed_cache: Optional[Set[str]] = None

        loaded = self._load_tools_from_json()
        if loaded:
            if self._looks_like_fallback_dataset(loaded):
                console.print("[yellow]ℹ️  Detected fallback dataset in JSON; triggering full discovery[/yellow]")
                loaded = []
            else:
                self.tools = loaded
                console.print(f"[green]✓ Loaded {len(loaded)} tools from JSON[/green]")

        if not loaded:
            # Start with an empty list and populate from discovery
            console.print("[cyan]🔍 First run detected - discovering tools from Kali sources...[/cyan]")
            self.tools = []
            web_count = 0
            try:
                # Discover from Kali tools website (best-effort)
                console.print("[dim]Fetching tool list from kali.org/tools/all-tools/...[/dim]")
                added = self.discover_from_kali_site()
                web_count = len(added)
                if web_count > 0:
                    console.print(f"[green]✓ Discovered {web_count} tools from website[/green]")
                else:
                    console.print("[yellow]⚠️  Web discovery returned no tools[/yellow]")
            except Exception as e:
                console.print(f"[yellow]⚠️  Web discovery failed: {e}[/yellow]")
            try:
                # Supplement from meta-packages and persist
                console.print("[dim]Checking meta-packages for additional tools...[/dim]")
                # Add tools from meta-packages to existing web-discovered tools
                meta_discovered = self._discover_tools_from_meta_packages()
                existing_names = {t.name for t in self.tools}
                added_count = 0
                for tool in meta_discovered:
                    if tool.name not in existing_names:
                        self.tools.append(tool)
                        existing_names.add(tool.name)
                        added_count += 1
                if added_count > 0:
                    console.print(f"[green]✓ Added {added_count} tools from meta-packages[/green]")
            except Exception as e:
                console.print(f"[yellow]⚠️  Meta-package scan failed: {e}[/yellow]")
            if not self.tools:
                # Absolute last-resort fallback to tiny embedded data
                console.print("[yellow]⚠️  No tools discovered, using minimal fallback[/yellow]")
                self.tools = self._parse_tools_data()
            # Ensure we have persisted whatever we discovered so that
            # subsequent runs load purely from JSON.
            try:
                self._save_tools_to_json(self.tools)
                console.print(f"[green]✓ Saved {len(self.tools)} tools to data/tools_merged.json[/green]")
            except Exception as e:
                console.print(f"[yellow]⚠️  Could not save tools: {e}[/yellow]")
            
            # Clear screen after discovery to prepare for UI display
            console.clear()
            
        self.cache_file = Path.home() / ".kali_tools_cache.json"
        self.local_repo_file = Path.home() / ".kali_tools_local_repo.txt"
        self.installation_status = {}
        self.config_manager = ConfigManager(self.tools)
        self.load_cache()
        self._categorize_tools()
        self.category_override_file = Path.home() / ".kali_tools_overrides.json"
        self.category_overrides: Dict[str, Dict[str, str]] = {}
        self.category_overrides = self._load_category_overrides()
        self.meta_hint_cache_file = Path.home() / ".kali_tools_meta_hints.json"
        self.meta_category_hints: Dict[str, Dict[str, str]] = self._load_meta_category_cache()
        if not self.meta_category_hints and self.is_debian_based():
            hints = self._discover_meta_category_hints()
            if hints:
                self.meta_category_hints = hints
                self._save_meta_category_cache(hints)
        self._apply_metadata_enrichment()
        self._load_local_repo()
        self.description_cache: Dict[str, str] = {}
        self._dependency_cache: Dict[str, List[str]] = {}
        self._package_size_cache: Dict[str, int] = {}
        self._check_system_requirements()
        # Purge legacy history database if present
        try:
            db_file = Path.home() / ".kali_tools_operations.db"
            if db_file.exists():
                db_file.unlink()
        except Exception:
            pass

    def _load_web_index(self) -> Optional[Dict[str, Any]]:
        """Load canonical tools index discovered from Kali website, if available.

        Expects a JSON object mapping normalized tool names to any metadata
        (typically written by discover_from_kali_site). Missing or invalid
        files are ignored.
        """
        try:
            base_dir = Path(__file__).resolve().parent
            data_dir = base_dir / "data"
            index_file = data_dir / "kali_web_index.json"
            if not index_file.exists():
                return None
            with open(index_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Normalize keys to lowercase strings
                return {str(k).lower(): v for k, v in data.items()}
        except Exception:
            return None
        return None

    def refresh_tools_from_sources(self) -> int:
        """Rebuild tools list from JSON and any available discovery sources.

        Uses three stages:
          1. Load existing tools from JSON (or embedded fallback).
          2. Discover additional tools from Kali meta-packages.
          3. Merge and persist the combined list back to a JSON file.

        Returns the number of *new* tools detected compared to the current list.
        """
        try:
            existing_names = {t['name'] for t in self.tools}
            base_tools = self._load_tools_from_json() or []

            # Discover additional tools from Kali meta-packages (best-effort)
            discovered = self._discover_tools_from_meta_packages()

            # Merge by name, preferring existing JSON definitions when present
            merged: Dict[str, Tool] = {t['name']: t for t in base_tools}
            for tool in discovered:
                name = tool['name']
                if name not in merged:
                    merged[name] = tool

            self.tools = list(merged.values())
            self._categorize_tools()
            new_names = {t['name'] for t in self.tools}
            added_names = new_names - existing_names
            added = len(added_names)
            # Update config manager with new tool set
            self.config_manager = ConfigManager(self.tools)

            # Persist merged tools to a primary JSON file for next runs
            self._save_tools_to_json(self.tools)
            # Return number of new tools detected
            return added
        except Exception as e:
            console.print(f"[red]Error refreshing tools from sources: {e}[/red]")
            return 0

    def _discover_tools_from_meta_packages(self) -> List[Tool]:
        """Best-effort discovery of tools from Kali meta-packages.

        This inspects dependencies of selected `kali-linux-*` meta-packages via
        `apt-cache depends` and returns a list of `Tool` instances for
        previously unseen package names. It intentionally does NOT include
        library-style packages (lib*, python3-*, etc.) based on simple
        heuristics.
        """
        meta_roots = deque([
            "kali-linux-top10",
            "kali-linux-default",
        ])
        visited_meta: Set[str] = set()
        discovered: Dict[str, Tool] = {}

        deny_prefixes = (
            "lib",
            "python",
            "fonts-",
            "firmware-",
            "linux-headers-",
        )
        hard_blocklist = {
            "kali-linux-headless",
            "kali-system-gui",
            "kali-tools-top10",
        }

        web_index = self.web_index or {}
        alias_map = {
            "metasploit-framework": "metasploit",
        }

        while meta_roots:
            meta = meta_roots.popleft()
            if meta in visited_meta or not meta:
                continue
            visited_meta.add(meta)
            try:
                result = subprocess.run(
                    ["apt-cache", "depends", meta],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except Exception:
                continue

            if result.returncode != 0:
                continue

            for line in result.stdout.splitlines():
                line = line.strip()
                if not line.startswith(("Depends:", "Recommends:")):
                    continue
                pkg = line.split(":", 1)[-1].strip()
                if not pkg or pkg in hard_blocklist:
                    continue
                if pkg.startswith(("kali-linux-", "kali-tools-")):
                    if pkg not in visited_meta:
                        meta_roots.append(pkg)
                    continue
                if pkg.startswith(deny_prefixes):
                    continue
                if pkg in discovered:
                    continue

                norm = pkg.lower()
                norm = alias_map.get(norm, norm)
                if web_index and norm not in web_index:
                    continue

                discovered[pkg] = Tool(name=pkg, commands=[pkg], installed=False, category="other", size=0)

        return list(discovered.values())

    def _save_tools_to_json(self, tools: List[Tool]) -> None:
        """Persist the merged tools list to a primary JSON file.

        This writes to `data/tools_merged.json`, creating the `data` directory
        if needed. Only basic fields are stored for now.
        """
        try:
            base_dir = Path(__file__).resolve().parent
            data_dir = base_dir / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            out_file = data_dir / "tools_merged.json"

            payload: List[Dict[str, Any]] = [t.to_dict() for t in tools]

            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            console.print(f"[yellow]⚠️ Could not persist merged tools JSON: {e}[/yellow]")

    def _looks_like_fallback_dataset(self, tools: List[Tool]) -> bool:
        """Detect whether the provided tools match the built-in fallback list."""
        names: Set[str] = set()
        for tool in tools:
            if isinstance(tool, Tool):
                name = tool.name
            else:
                name = str(tool.get('name', '')).strip()  # type: ignore[attr-defined]
            if not name:
                return False
            names.add(name.lower())
        if not names:
            return False
        return any(names == variant for variant in FALLBACK_NAME_VARIANTS)

    def _parse_tools_data(self) -> List[Tool]:
        """Return a minimal built-in tool list for fully offline scenarios."""
        return [Tool.from_dict(entry) for entry in FALLBACK_TOOL_ENTRIES]

    def remove_tool_from_list(self, tool_name: str) -> bool:
        """Remove a tool from the main list and persist the change.

        This is a logical removal from the manager's view (and JSON), not an
        uninstall from the system.
        """
        original_len = len(self.tools)
        self.tools = [t for t in self.tools if t['name'] != tool_name]
        if len(self.tools) < original_len:
            self._save_tools_to_json(self.tools)
            self.config_manager = ConfigManager(self.tools)
            return True
        return False
    # Discovery via Kali tools website only (local apt-cache discovery removed)
    
    def _check_system_requirements(self):
        """Check if system meets requirements on startup"""
        if not self.is_debian_based():
            console.print("[yellow]⚠️  Warning: This tool is designed for Debian/Ubuntu-based systems (Kali Linux)[/yellow]")
            console.print("[dim]Some features may not work correctly on other distributions[/dim]\n")
        
        if not shutil.which('apt-get'):
            console.print("[red]❌ Error: apt-get not found![/red]")
            console.print("[yellow]This tool requires apt-get package manager[/yellow]\n")
        
        if not shutil.which('dpkg'):
            console.print("[red]❌ Error: dpkg not found![/red]")
            console.print("[yellow]This tool requires dpkg package manager[/yellow]\n")
    
    def is_debian_based(self) -> bool:
        """Check if system is Debian-based"""
        try:
            return bool(shutil.which('apt-get') and shutil.which('dpkg'))
        except Exception:
            return False
    
    def check_sudo_available(self) -> bool:
        """Check if sudo is available and user can use it"""
        try:
            if not shutil.which('sudo'):
                console.print("[red]❌ Error: sudo command not found![/red]")
                console.print("[yellow]Please install sudo: apt install sudo[/yellow]")
                return False
            
            result = subprocess.run(
                ['sudo', '-n', 'true'],
                capture_output=True,
                timeout=1
            )
            
            return True
            
        except subprocess.TimeoutExpired:
            return True  # Assume it's working, just waiting for password
        except Exception as e:
            console.print(f"[yellow]⚠️  Warning: Could not verify sudo access: {e}[/yellow]")
            return True  # Assume it's available to avoid blocking
    
    def verify_sudo_before_operation(self) -> bool:
        """Verify sudo access before performing privileged operations"""
        try:
            result = subprocess.run(
                ['sudo', '-v'],  # Refresh sudo timestamp
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return True
            else:
                console.print("[red]❌ Sudo authentication failed![/red]")
                console.print("[yellow]Please ensure:[/yellow]")
                console.print("  1. You have sudo privileges")
                console.print("  2. Your password is correct")
                console.print("  3. Your user is in the sudoers file")
                return False
                
        except subprocess.TimeoutExpired:
            console.print("[red]❌ Sudo authentication timed out![/red]")
            return False
        except Exception as e:
            console.print(f"[red]❌ Error verifying sudo: {e}[/red]")
            return False

    # --- Tool data loading ---

    def _load_tools_from_json(self) -> List[Tool]:
        """Load tools from JSON files in a local data directory.

        Looks for any `tools_*.json` under a `data` folder next to this script.
        Each file should contain a list of objects with at least:
          - name: str
          - commands: list[str] (optional)
          - category: str (optional)
          - source: str (optional, e.g. "kali")
        """
        try:
            base_dir = Path(__file__).resolve().parent
            data_dir = base_dir / "data"
            if not data_dir.exists():
                return []

            tools: List[Tool] = []
            for json_file in sorted(data_dir.glob("tools_*.json")):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        payload = json.load(f)
                except Exception as e:
                    console.print(f"[yellow]⚠️ Could not read {json_file.name}: {e}[/yellow]")
                    continue

                if not isinstance(payload, list):
                    console.print(f"[yellow]⚠️ {json_file.name} must contain a JSON list of tool objects[/yellow]")
                    continue

                for entry in payload:
                    if not isinstance(entry, dict):
                        continue
                    tool = Tool.from_dict(entry)
                    if not tool.name:
                        continue
                    tools.append(tool)

            return tools
        except Exception as e:
            console.print(f"[yellow]⚠️ Error loading JSON tools: {e}[/yellow]")
            return []


    def _categorize_tools(self):
        """Normalize tool metadata (category, subcategory, description, commands)."""
        lookup = self._build_category_lookup()
        for idx, raw_tool in enumerate(self.tools):
            tool = raw_tool
            if not isinstance(tool, Tool):
                tool = Tool.from_dict(tool)  # type: ignore[arg-type]
                self.tools[idx] = tool
            self._normalize_tool_entry(tool, lookup)

    @staticmethod
    def _build_category_lookup() -> Dict[str, str]:
        lookup: Dict[str, str] = {}
        for category, names in CATEGORIES.items():
            for name in names:
                lookup[name.lower()] = category
        return lookup

    @staticmethod
    def _dedupe_preserve_order(values: List[str]) -> List[str]:
        seen: Set[str] = set()
        result: List[str] = []
        for value in values:
            text = str(value or '').strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
        return result

    def _normalize_tool_entry(self, tool: Tool, lookup: Dict[str, str]) -> None:
        tool.name = tool.name.strip()
        if not tool.name:
            return

        tool.commands = self._dedupe_preserve_order(tool.commands or [tool.name])
        if tool.name and not any(cmd.lower() == tool.name.lower() for cmd in tool.commands):
            tool.commands.insert(0, tool.name)

        mapped_category = lookup.get(tool.name.lower())
        current_category = (tool.category or '').strip().lower()
        if mapped_category and (current_category in ('', 'other') or current_category not in CATEGORY_NAMES):
            tool.category = mapped_category
        elif current_category and current_category in CATEGORY_NAMES:
            tool.category = current_category
        else:
            tool.category = mapped_category or 'other'

        if not tool.subcategory:
            tool.subcategory = get_subcategory_for(tool.name, tool.category) or ''

        if not tool.description:
            tool.description = TOOL_DESCRIPTIONS.get(tool.name, '')

        tool.subpackages = self._dedupe_preserve_order(tool.subpackages)

        # Cache commonly used metadata hints for UI renderers
        icon = CATEGORY_ICONS.get(tool.category or 'other', CATEGORY_ICONS['other'])
        tool.metadata.setdefault('icon', icon)
        tool.metadata.setdefault('category_display', get_category_display_name(tool.category))

    def _apply_metadata_enrichment(self) -> None:
        """Enhance category/subcategory metadata via heuristics and overrides."""
        for idx, entry in enumerate(self.tools):
            tool = entry
            if not isinstance(tool, Tool):
                tool = Tool.from_dict(entry)  # type: ignore[arg-type]
                self.tools[idx] = tool
            self._infer_metadata_for_tool(tool)
            self._apply_override_to_tool(tool)

    def _infer_metadata_for_tool(self, tool: Tool) -> None:
        haystack = self._build_metadata_haystack(tool)
        current_category = (tool.category or '').lower()
        meta_hint = self.meta_category_hints.get(tool.name.lower())
        if meta_hint:
            hinted_category = meta_hint.get('category')
            hinted_subcategory = meta_hint.get('subcategory')
            if hinted_category and self._should_replace_category(current_category):
                tool.category = hinted_category
                current_category = hinted_category
            if hinted_subcategory and not tool.subcategory:
                tool.subcategory = hinted_subcategory
        if (not current_category or current_category == 'other' or current_category not in CATEGORY_NAMES) and haystack:
            guessed = self._match_category_from_keywords(haystack)
            if guessed:
                tool.category = guessed
                current_category = guessed

        if not tool.subcategory and haystack:
            guessed_sub = self._match_subcategory_from_keywords(tool.category, haystack)
            if guessed_sub:
                tool.subcategory = guessed_sub

        if not tool.subcategory:
            fallback = get_subcategory_for(tool.name, tool.category)
            if not fallback and meta_hint:
                fallback = meta_hint.get('subcategory') or ''
            if fallback:
                tool.subcategory = fallback
            else:
                default_sub = CATEGORY_DEFAULT_SUBCATEGORY.get((tool.category or 'other').lower())
                if default_sub:
                    tool.subcategory = default_sub

        self._refresh_tool_metadata(tool)

    def _apply_override_to_tool(self, tool: Tool) -> None:
        override = self.category_overrides.get(tool.name.lower())
        if not override:
            return

        override_category = (override.get('category') or '').strip().lower()
        if override_category in CATEGORY_NAMES:
            tool.category = override_category
        elif override_category:
            tool.category = 'other'

        if 'subcategory' in override:
            tool.subcategory = (override.get('subcategory') or '').strip()
            if not tool.subcategory:
                inferred = get_subcategory_for(tool.name, tool.category)
                if inferred:
                    tool.subcategory = inferred

        self._refresh_tool_metadata(tool)

    def _build_metadata_haystack(self, tool: Tool) -> str:
        parts: List[str] = [tool.name, ' '.join(tool.commands or []), tool.description, ' '.join(tool.subpackages or [])]
        meta_keywords = tool.metadata.get('keywords') if isinstance(tool.metadata, dict) else None  # type: ignore[arg-type]
        if isinstance(meta_keywords, list):
            parts.append(' '.join(str(item) for item in meta_keywords if item))
        elif isinstance(meta_keywords, str):
            parts.append(meta_keywords)
        return ' '.join(part for part in parts if part).lower()

    @staticmethod
    def _match_category_from_keywords(haystack: str) -> Optional[str]:
        for category, keywords in CATEGORY_KEYWORD_HINTS.items():
            for keyword in keywords:
                if keyword.lower() in haystack:
                    return category
        return None

    @staticmethod
    def _match_subcategory_from_keywords(category: Optional[str], haystack: str) -> Optional[str]:
        if not category:
            return None
        mapping = SUBCATEGORY_KEYWORD_HINTS.get((category or '').lower(), {})
        for subcategory, keywords in mapping.items():
            for keyword in keywords:
                if keyword.lower() in haystack:
                    return subcategory
        return None

    def _refresh_tool_metadata(self, tool: Tool) -> None:
        if not isinstance(tool.metadata, dict):
            tool.metadata = {}
        icon = CATEGORY_ICONS.get(tool.category or 'other', CATEGORY_ICONS['other'])
        tool.metadata['icon'] = icon
        tool.metadata['category_display'] = get_category_display_name(tool.category)

    @staticmethod
    def _should_replace_category(current_category: Optional[str]) -> bool:
        slug = (current_category or '').strip().lower()
        return not slug or slug == 'other' or slug not in CATEGORY_NAMES

    def _load_category_overrides(self) -> Dict[str, Dict[str, str]]:
        if not self.category_override_file.exists():
            return {}
        try:
            with open(self.category_override_file, 'r', encoding='utf-8') as handle:
                raw_data = json.load(handle)
        except Exception as exc:
            console.print(f"[yellow]⚠️ Could not read category overrides: {exc}[/yellow]")
            return {}

        overrides: Dict[str, Dict[str, str]] = {}
        if isinstance(raw_data, dict):
            for name, payload in raw_data.items():
                if not isinstance(payload, dict):
                    continue
                category = str(payload.get('category', '') or '').strip().lower()
                subcategory = str(payload.get('subcategory', '') or '').strip()
                if category and category not in CATEGORY_NAMES:
                    category = 'other'
                overrides[name.lower()] = {
                    'category': category or 'other',
                    'subcategory': subcategory,
                    'original_name': name,
                }
        return overrides

    def _save_category_overrides(self) -> None:
        if not self.category_overrides:
            try:
                if self.category_override_file.exists():
                    self.category_override_file.unlink()
            except Exception:
                pass
            return

        payload: Dict[str, Dict[str, str]] = {}
        for key, values in self.category_overrides.items():
            name = values.get('original_name') or self._lookup_tool_name(key)
            if not name:
                continue
            payload[name] = {
                'category': values.get('category', 'other'),
                'subcategory': values.get('subcategory', ''),
            }

        try:
            with open(self.category_override_file, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, indent=2)
        except Exception as exc:
            console.print(f"[yellow]⚠️ Could not persist category overrides: {exc}[/yellow]")

    def _load_meta_category_cache(self, ttl_hours: int = 240) -> Dict[str, Dict[str, str]]:
        if not hasattr(self, 'meta_hint_cache_file'):
            return {}
        cache_path = self.meta_hint_cache_file
        if not cache_path.exists():
            return {}
        try:
            with open(cache_path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except Exception:
            return {}

        timestamp = float(data.get('timestamp', 0) or 0)
        if ttl_hours > 0 and timestamp:
            if (time.time() - timestamp) > (ttl_hours * 3600):
                return {}

        raw_hints = data.get('hints') or {}
        if not isinstance(raw_hints, dict):
            return {}

        normalized: Dict[str, Dict[str, str]] = {}
        for name, payload in raw_hints.items():
            category = ''
            subcategory = ''
            if isinstance(payload, dict):
                category = str(payload.get('category', '') or '').strip().lower()
                subcategory = str(payload.get('subcategory', '') or '').strip()
            else:
                category = str(payload or '').strip().lower()
            if category not in CATEGORY_NAMES:
                category = 'other'
            normalized[str(name).lower()] = {
                'category': category,
                'subcategory': subcategory,
            }
        return normalized

    def _save_meta_category_cache(self, hints: Dict[str, Dict[str, str]]) -> None:
        if not hasattr(self, 'meta_hint_cache_file'):
            return
        payload = {
            'timestamp': time.time(),
            'hints': hints,
        }
        try:
            with open(self.meta_hint_cache_file, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, indent=2)
        except Exception as exc:
            console.print(f"[yellow]⚠️ Could not persist meta category cache: {exc}[/yellow]")

    def _discover_meta_category_hints(self) -> Dict[str, Dict[str, str]]:
        hints: Dict[str, Dict[str, str]] = {}
        if not shutil.which('apt-cache'):
            return hints

        deny_prefixes = ('fonts-', 'firmware-', 'lib', 'python', 'gir1.2-', 'doc-')

        for meta_pkg, mapping in META_CATEGORY_SOURCES.items():
            if isinstance(mapping, tuple):
                category, subcategory_default = mapping
            else:
                category = mapping
                subcategory_default = ''
            try:
                result = subprocess.run(
                    ['apt-cache', 'depends', meta_pkg],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except Exception:
                continue
            if result.returncode != 0:
                continue

            slug = category if category in CATEGORY_NAMES else 'other'
            for line in result.stdout.splitlines():
                text_line = line.strip()
                if not text_line.startswith(('Depends:', 'Recommends:')):
                    continue
                pkg = text_line.split(':', 1)[-1].strip()
                if not pkg or pkg.startswith('kali-tools-') or pkg.startswith('kali-linux-'):
                    continue
                if pkg.startswith(deny_prefixes):
                    continue
                hints.setdefault(pkg.lower(), {
                    'category': slug,
                    'subcategory': subcategory_default,
                })
        return hints

    def _lookup_tool_name(self, key: str) -> Optional[str]:
        key = (key or '').lower()
        for tool in self.tools:
            name = ''
            try:
                name = tool['name']  # type: ignore[index]
            except Exception:
                name = getattr(tool, 'name', '')
            if name and name.lower() == key:
                return name
        return None

    def set_tool_category_override(
        self,
        tool_name: str,
        category: Optional[str],
        subcategory: Optional[str] = None,
    ) -> None:
        """Persist a user-defined category/subcategory for a specific tool."""
        normalized = (tool_name or '').strip()
        if not normalized:
            return
        key = normalized.lower()

        if category is None:
            if key in self.category_overrides:
                self.category_overrides.pop(key, None)
                tool = next((t for t in self.tools if getattr(t, 'name', '').lower() == key), None)
                if tool:
                    self._infer_metadata_for_tool(tool)
            self._save_category_overrides()
            return

        category_slug = (category or '').strip().lower()
        if category_slug not in CATEGORY_NAMES:
            category_slug = 'other'

        sub_text = (subcategory or '').strip()

        self.category_overrides[key] = {
            'category': category_slug,
            'subcategory': sub_text,
            'original_name': normalized,
        }

        tool = next((t for t in self.tools if getattr(t, 'name', '').lower() == key), None)
        if tool:
            tool.category = category_slug
            if sub_text:
                tool.subcategory = sub_text
            else:
                inferred = self._match_subcategory_from_keywords(category_slug, self._build_metadata_haystack(tool))
                tool.subcategory = inferred or get_subcategory_for(tool.name, category_slug) or ''
            self._refresh_tool_metadata(tool)

        self._save_category_overrides()

    def _load_local_repo(self):
        """Load local repository path if configured"""
        if self.local_repo_file.exists():
            try:
                with open(self.local_repo_file, 'r') as f:
                    self.local_repo = f.read().strip()
            except Exception:
                self.local_repo = None
        else:
            self.local_repo = None

    def fetch_tools_from_web(self) -> bool:
        """Fetch latest tools from Kali website using web scraping"""
        if not WEB_SCRAPING_AVAILABLE:
            console.print("[yellow]Web scraping not available. Install: pip install requests beautifulsoup4[/yellow]")
            return False
        
        try:
            console.print("[cyan]Fetching tools from Kali website...[/cyan]")
            url = "https://www.kali.org/tools/all-tools/"
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                task = progress.add_task("[cyan]Downloading page...", total=None)
                response = requests.get(url, timeout=10)
                progress.update(task, completed=True)
            
            if response.status_code != 200:
                console.print(f"[red]Failed to fetch page. Status code: {response.status_code}[/red]")
                return False
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            console.print("[green]✓ Successfully fetched tools data[/green]")
            console.print("[yellow]Note: Web scraping integration needs HTML structure analysis[/yellow]")
            return True
            
        except requests.RequestException as e:
            console.print(f"[red]Network error: {e}[/red]")
            return False
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            return False

    def load_cache(self):
        """Load cached installation status"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    self.installation_status = json.load(f)
                for tool in self.tools:
                    tool.installed = self.installation_status.get(tool.name, False)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not load cache: {e}[/yellow]")

    def save_cache(self):
        """Save installation status to cache"""
        try:
            self.installation_status = {tool.name: tool.installed for tool in self.tools}
            with open(self.cache_file, 'w') as f:
                json.dump(self.installation_status, f, indent=2)
        except Exception as e:
            console.print(f"[yellow]Warning: Could not save cache: {e}[/yellow]")

    def refresh_installed_cache(self, force: bool = False) -> Set[str]:
        """Return cached dpkg package list, refreshing when needed."""
        if self._installed_cache is not None and not force:
            return self._installed_cache

        installed: Set[str] = set()
        try:
            result = subprocess.run(['dpkg', '-l'], capture_output=True, text=True, timeout=15)
            for line in result.stdout.splitlines():
                if line.startswith('ii'):
                    parts = line.split()
                    if len(parts) >= 2:
                        installed.add(parts[1])
        except Exception as exc:
            logger.debug("dpkg cache refresh failed: %s", exc)

        self._installed_cache = installed
        return installed

    def check_installation(self, package_name: str) -> bool:
        """Check if a package is installed using dpkg"""
        installed_cache = self.refresh_installed_cache()
        if installed_cache:
            return package_name in installed_cache
        try:
            result = subprocess.run(
                ['dpkg', '-l', package_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            return 'ii' in result.stdout and package_name in result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # Rating functionality removed

    def get_dependencies(self, package_name: str) -> List[str]:
        """Get package dependencies"""
        if package_name in self._dependency_cache:
            return self._dependency_cache[package_name]
        try:
            result = subprocess.run(
                ['apt-cache', 'depends', package_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                return []
            
            dependencies = []
            for line in result.stdout.split('\n'):
                if line.strip().startswith('Depends:'):
                    dep = line.split(':')[1].strip()
                    dependencies.append(dep)
            
            self._dependency_cache[package_name] = dependencies
            return dependencies
        except Exception:
            return []

    def get_package_size(self, package_name: str) -> int:
        """Get installed package size in bytes"""
        cached = self._package_size_cache.get(package_name)
        if cached is not None:
            return cached

        size = self._query_installed_size(package_name)
        if size == 0:
            size = self._query_repo_size(package_name)

        self._package_size_cache[package_name] = size
        return size

    def _query_installed_size(self, package_name: str) -> int:
        try:
            result = subprocess.run(
                ['dpkg-query', '-W', '-f=${Installed-Size}', package_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except FileNotFoundError:
            return 0
        except Exception:
            return 0
        if result.returncode != 0:
            return 0
        tokens = (result.stdout or '').strip().split()
        if not tokens:
            return 0
        try:
            size_kb = int(tokens[0])
        except (TypeError, ValueError):
            return 0
        return max(0, size_kb) * 1024

    def _query_repo_size(self, package_name: str) -> int:
        try:
            result = subprocess.run(
                ['apt-cache', 'show', package_name],
                capture_output=True,
                text=True,
                timeout=7,
            )
        except FileNotFoundError:
            return 0
        except Exception:
            return 0
        if result.returncode != 0:
            return 0
        for line in result.stdout.splitlines():
            if line.startswith('Installed-Size:'):
                tokens = line.split(':', 1)[1].strip().split()
                if not tokens:
                    continue
                try:
                    size_kb = int(tokens[0])
                    return max(0, size_kb) * 1024
                except (TypeError, ValueError):
                    continue
        return 0

    def scan_all_tools(self) -> Tuple[int, int]:
        """Fast scan of all tools using single dpkg -l parse (no per-package calls)."""
        installed_count = 0
        try:
            result = subprocess.run(['dpkg', '-l'], capture_output=True, text=True, timeout=15)
            lines = result.stdout.splitlines()
            installed_set = set()
            for line in lines:
                if line.startswith('ii'):
                    parts = line.split()
                    if len(parts) >= 2:
                        installed_set.add(parts[1])

            with Progress(
                SpinnerColumn(),
                TextColumn('[progress.description]{task.description}'),
                BarColumn(),
                TextColumn('[progress.percentage]{task.percentage:>3.0f}%'),
                console=console
            ) as progress:
                task = progress.add_task('[cyan]🔍 Scanning installed packages (dpkg cache)...', total=len(self.tools))
                for tool in self.tools:
                    tool.installed = tool.name in installed_set
                    if tool.installed:
                        installed_count += 1
                        tool.size = self.get_package_size(tool.name)
                    progress.update(task, advance=1)
            self._installed_cache = installed_set
        except Exception:
            for tool in self.tools:
                tool.installed = self.check_installation(tool.name)
                if tool.installed:
                    installed_count += 1
                    tool.size = self.get_package_size(tool.name)

        self.save_cache()
        return installed_count, len(self.tools)

    def validate_tool_name(self, name: str) -> bool:
        """Validate tool name format"""
        if not name or not name.strip():
            console.print("[red]❌ Tool name cannot be empty![/red]")
            return False
        if not re.match(r'^[a-z0-9\-+.]+$', name):
            console.print("[red]❌ Invalid tool name format! Use only lowercase letters, numbers, hyphens, and dots.[/red]")
            return False
        return True

    def check_disk_space(self, required_mb: int = 100) -> bool:
        """Check if sufficient disk space is available"""
        if not PSUTIL_AVAILABLE:
            return True  # Can't check, assume OK
        
        try:
            disk = psutil.disk_usage('/')
            available_mb = disk.free / 1024 / 1024
            
            if available_mb < required_mb + 500:  # 500MB safety buffer
                console.print(f"[red]❌ Insufficient disk space![/red]")
                console.print(f"[yellow]Available: {available_mb:.0f} MB | Required: ~{required_mb} MB + 500 MB buffer[/yellow]")
                console.print(f"[dim]Tip: Free up space or use 'apt-get clean' to remove cached packages[/dim]")
                return False
            
            if available_mb < 2000:  # Warn if less than 2GB
                console.print(f"[yellow]⚠️  Low disk space: {available_mb:.0f} MB available[/yellow]")
            
            return True
        except Exception as e:
            console.print(f"[yellow]⚠️  Could not check disk space: {e}[/yellow]")
            return True

    def install_tool(self, package_name: str) -> bool:
        """Install a tool using apt-get with progress tracking"""
        try:
            if not self.validate_tool_name(package_name):
                return False
            
            if not self.verify_sudo_before_operation():
                console.print("[red]❌ Cannot proceed without sudo privileges[/red]")
                return False
            
            tool = next((t for t in self.tools if t['name'] == package_name), None)
            if not tool:
                console.print(f"[red]❌ Tool '{package_name}' not found in database![/red]")
                console.print(f"[dim]Tip: Use 'S' to search for similar tools[/dim]")
                return False
            
            if tool['installed']:
                console.print(f"[yellow]ℹ️  {package_name} is already installed![/yellow]")
                return False
            
            if not self.check_disk_space(100):
                if not Confirm.ask("[yellow]Continue anyway?[/yellow]", default=False):
                    return False
            
            console.print(f"\n[yellow]Installing {package_name}...[/yellow]")
            console.print("[dim]This requires sudo privileges[/dim]\n")
            
            deps = self.get_dependencies(package_name)
            if deps:
                console.print(f"[cyan]Dependencies ({len(deps)}): {', '.join(deps[:5])}{' ...' if len(deps) > 5 else ''}[/cyan]\n")
            
            start_time = time.time()
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                console=console
            ) as progress:
                task = progress.add_task(f"[cyan]Installing {package_name}...", total=100)
                
                process = subprocess.Popen(
                    ['sudo', 'apt-get', 'install', '-y', package_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                
                progress_value = 0
                line_count = 0
                for line in process.stdout:
                    line_count += 1
                    if 'Reading package lists' in line or 'Reading' in line:
                        progress_value = max(progress_value, 15)
                    elif 'Building dependency tree' in line or 'Building' in line:
                        progress_value = max(progress_value, 25)
                    elif 'Reading state information' in line or 'state' in line:
                        progress_value = max(progress_value, 35)
                    elif 'Need to get' in line or 'Get:' in line:
                        progress_value = max(progress_value, 45)
                    elif 'Unpacking' in line or 'Selecting' in line:
                        progress_value = max(progress_value, 60)
                    elif 'Setting up' in line or 'Preparing' in line:
                        progress_value = max(progress_value, 80)
                    elif 'Processing triggers' in line or 'triggers' in line:
                        progress_value = max(progress_value, 90)
                    
                    estimated_progress = min(95, 5 + (line_count * 2))
                    progress_value = max(progress_value, estimated_progress)
                    
                    progress.update(task, completed=progress_value)
                
                process.wait()
                progress.update(task, completed=100)
                result_code = process.returncode
            
            elapsed_time = time.time() - start_time
            
            success = result_code == 0
            
            if success:
                for tool in self.tools:
                    if tool['name'] == package_name:
                        tool['installed'] = True
                        tool['size'] = self.get_package_size(package_name)
                
                self.save_cache()
                self._installed_cache = None
                console.print(f"\n[green]✅ {package_name} installed successfully in {elapsed_time:.1f}s![/green]")
                
                if tool['commands']:
                    console.print(f"[cyan]💡 Available commands: {', '.join(tool['commands'][:3])}[/cyan]")
                
                if notifications_ready():
                    send_notification(
                        "Installation Complete",
                        f"{package_name} has been successfully installed",
                    )
                
            else:
                console.print(f"\n[red]❌ Failed to install {package_name}[/red]")
                console.print("\n[yellow]🔧 Troubleshooting tips:[/yellow]")
                console.print("  1. Check your internet connection")
                console.print("  2. Update package lists: sudo apt-get update")
                console.print("  3. Verify package name is correct")
                console.print("  4. Ensure you have sudo privileges: sudo -v")
                console.print("  5. Check if another apt process is running: ps aux | grep apt")
                console.print(f"  6. Try manually: sudo apt-get install {package_name}")
                console.print("  7. Check system logs: journalctl -xe")
            
            return success
        except subprocess.TimeoutExpired:
            console.print(f"[red]❌ Installation timed out![/red]")
            console.print("[yellow]⚠️  Possible causes:[/yellow]")
            console.print("  • Package manager is locked by another process")
            console.print("  • Waiting for sudo password (if running in background)")
            console.print("  • Network connection issues")
            console.print("[dim]Tip: Check running processes: ps aux | grep -E 'apt|dpkg'[/dim]")
            return False
        except FileNotFoundError:
            console.print(f"[red]❌ Command not found![/red]")
            console.print("[yellow]This tool requires:[/yellow]")
            console.print("  • Debian/Ubuntu-based system (Kali Linux)")
            console.print("  • apt-get package manager")
            console.print("  • sudo command")
            console.print("[dim]Current system may not be compatible[/dim]")
            return False
        except PermissionError:
            console.print(f"[red]❌ Permission denied![/red]")
            console.print("[yellow]⚠️  Sudo privileges required:[/yellow]")
            console.print("  1. Verify you're in sudoers: groups $USER")
            console.print("  2. Test sudo access: sudo -v")
            console.print("  3. Add user to sudo group: sudo usermod -aG sudo $USER")
            console.print("  4. Check sudoers file: sudo visudo")
            return False
        except Exception as e:
            console.print(f"[red]❌ Error: {e}[/red]")
            console.print(f"[dim]If the problem persists, re-run with verbose logging or check system logs[/dim]")
            return False

    def uninstall_tool(self, package_name: str) -> bool:
        """Uninstall a tool using apt-get with dependency handling"""
        try:
            if not self.verify_sudo_before_operation():
                console.print("[red]❌ Cannot proceed without sudo privileges[/red]")
                return False
            
            console.print(f"\n[yellow]Uninstalling {package_name}...[/yellow]")
            console.print("[dim]This requires sudo privileges[/dim]\n")
            
            start_time = time.time()
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                console=console
            ) as progress:
                task = progress.add_task(f"[yellow]Removing {package_name}...", total=100)
                
                process = subprocess.Popen(
                    ['sudo', 'apt-get', 'remove', '-y', package_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1
                )
                
                progress_value = 0
                stderr_output = []
                line_count = 0
                
                for line in process.stdout:
                    line_count += 1
                    if 'Reading package lists' in line or 'Reading' in line:
                        progress_value = max(progress_value, 20)
                    elif 'Building dependency tree' in line or 'Building' in line:
                        progress_value = max(progress_value, 35)
                    elif 'Reading state information' in line or 'state' in line:
                        progress_value = max(progress_value, 50)
                    elif 'Removing' in line or 'Purging' in line:
                        progress_value = max(progress_value, 75)
                    elif 'Processing triggers' in line or 'triggers' in line:
                        progress_value = max(progress_value, 90)
                    
                    estimated_progress = min(95, 10 + (line_count * 3))
                    progress_value = max(progress_value, estimated_progress)
                    
                    progress.update(task, completed=progress_value)
                
                stderr_output = process.stderr.read()
                process.wait()
                progress.update(task, completed=100)
                result_code = process.returncode
            
            if result_code != 0:
                error_output = stderr_output.lower()
                
                if 'unmet dependencies' in error_output or 'pkgproblemresolver' in error_output:
                    console.print(f"[yellow]⚠️  {package_name} is required by other packages[/yellow]\n")
                    console.print("[dim]This tool is part of a larger metapackage (like kali-desktop-xfce)[/dim]")
                    console.print("[dim]Removing it may affect your desktop environment.[/dim]\n")
                    
                    from rich.prompt import Confirm
                    if Confirm.ask("Try force removal? (This may remove dependent packages)", default=False):
                        console.print("\n[yellow]Attempting force removal with autoremove...[/yellow]\n")
                        
                        with Progress(
                            SpinnerColumn(),
                            TextColumn("[progress.description]{task.description}"),
                            BarColumn(),
                            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                            TimeElapsedColumn(),
                            console=console
                        ) as progress:
                            task = progress.add_task(f"[yellow]Force removing {package_name}...", total=100)
                            
                            process = subprocess.Popen(
                                ['sudo', 'apt-get', 'autoremove', '-y', package_name],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                bufsize=1
                            )
                            
                            progress_value = 0
                            for line in process.stdout:
                                if 'Reading' in line:
                                    progress_value = 20
                                elif 'Removing' in line:
                                    progress_value = 60
                                elif 'Processing' in line:
                                    progress_value = 90
                                progress.update(task, completed=progress_value)
                            
                            stderr_output = process.stderr.read()
                            process.wait()
                            progress.update(task, completed=100)
                            result_code = process.returncode
                        
                        if result_code != 0:
                            console.print(f"[red]✗ Unable to remove {package_name}[/red]\n")
                            console.print("[yellow]💡 Suggested alternatives:[/yellow]")
                            console.print("   1. This tool may be essential to your system")
                            console.print("   2. Consider marking it as 'hold' instead of removing")
                            console.print(f"   3. Manual removal: [cyan]sudo apt-get purge {package_name}[/cyan]")
                            console.print("   4. Check what depends on it: [cyan]apt-cache rdepends " + package_name + "[/cyan]")
                            return False
                    else:
                        console.print("[yellow]Uninstallation cancelled[/yellow]")
                        return False
                else:
                    console.print(f"\n[red]✗ Failed to uninstall {package_name}[/red]")
                    console.print(f"[dim]Error: {stderr_output[:200]}[/dim]")
                    return False
            
            elapsed_time = time.time() - start_time
            
            for tool in self.tools:
                if tool['name'] == package_name:
                    tool['installed'] = False
                    tool['size'] = 0
            
            self.save_cache()
            self._installed_cache = None
            console.print(f"\n[green]✓ {package_name} uninstalled successfully in {elapsed_time:.1f}s![/green]")
            
            if notifications_ready():
                send_notification(
                    "Uninstallation Complete",
                    f"{package_name} has been removed",
                )
            
            return True
            
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            return False

    def check_updates(self, progress_callback: Optional[Callable[[str, int, int], None]] = None) -> List[str]:
        """Check for available package updates with optional progress reporting."""

        def emit(message: str, completed: int, total: int) -> None:
            if progress_callback:
                try:
                    progress_callback(message, completed, total)
                    return
                except Exception:
                    pass
            console.print(f"[cyan]{message}[/cyan]")

        steps_total = 3
        emit("Refreshing package lists...", 0, steps_total)

        update_cmd = ['apt-get', 'update']
        geteuid = getattr(os, 'geteuid', None)
        is_root = False
        if callable(geteuid):
            try:
                is_root = geteuid() == 0
            except Exception:
                is_root = False
        if not is_root and shutil.which('sudo'):
            update_cmd.insert(0, 'sudo')

        try:
            update_proc = subprocess.run(
                update_cmd,
                capture_output=True,
                text=True,
                timeout=240,
            )
        except subprocess.TimeoutExpired:
            console.print("[red]apt-get update timed out after 4 minutes.[/red]")
            return []
        except Exception as e:
            console.print(f"[red]Failed to run {' '.join(update_cmd)}: {e}[/red]")
            return []

        if update_proc.returncode != 0:
            stderr = (update_proc.stderr or '').strip().splitlines()[:3]
            if stderr:
                console.print(f"[yellow]apt-get update reported issues:\n  {'\n  '.join(stderr)}[/yellow]")
            return []

        emit("Scanning upgradable packages...", 1, steps_total)

        try:
            result = subprocess.run(
                ['apt', 'list', '--upgradable'],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            console.print("[red]'apt list --upgradable' timed out.[/red]")
            return []
        except Exception as e:
            console.print(f"[red]Error running apt list: {e}[/red]")
            return []

        if result.returncode != 0:
            console.print("[red]apt list --upgradable failed.[/red]")
            return []

        emit("Processing results...", 2, steps_total)

        upgradable = []
        for line in result.stdout.split('\n')[1:]:  # Skip header
            if line.strip() and '/' in line:
                package = line.split('/')[0].strip()
                if any(t['name'] == package for t in self.tools):
                    upgradable.append(package)

        emit("Update check complete", steps_total, steps_total)
        return upgradable

    def get_tool_info(self, package_name: str) -> Optional[str]:
        """Get detailed information about a package"""
        try:
            result = subprocess.run(
                ['apt-cache', 'show', package_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.stdout if result.returncode == 0 else None
        except Exception:
            return None

    # Discovery logic removed (apt-cache based). Future implementation should scrape Kali tools site.

    # ---------- Discovery from Kali tools website ----------
    def _kali_site_cache_path(self) -> Path:
        cache_dir = Path.home() / '.cache' / 'kalitools'
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return cache_dir / 'kali_site_cache.json'

    def _load_kali_site_cache(self) -> Optional[Dict[str, Any]]:
        path = self._kali_site_cache_path()
        if path.exists():
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def _save_kali_site_cache(self, data: Dict[str, Any]):
        try:
            with open(self._kali_site_cache_path(), 'w') as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _fetch_kali_tool_links(self, max_pages: int = 5) -> List[str]:
        """Return list of tool page URLs from kali.org/tools/all-tools/."""
        if not WEB_SCRAPING_AVAILABLE or requests is None or BeautifulSoup is None:
            return []
        base = 'https://www.kali.org'
        urls: List[str] = []
        try:
            headers = {'User-Agent': 'kalitools-cli/0.1 (+https://example.local)'}
            # The all-tools page appears to be a single long page with all tools
            index_url = f"{base}/tools/all-tools/"
            
            if self.discovery_delay:
                time.sleep(self.discovery_delay)
            resp = requests.get(index_url, timeout=15, headers=headers)
            if resp.status_code != 200:
                return []
            
            soup = BeautifulSoup(resp.content, 'html.parser')
            
            # Debug counters
            total_links = 0
            tools_links = 0
            sample_hrefs = []
            
            # Find all links that point to individual tool pages
            # Pattern: /tools/<toolname>/ (exactly 3 parts, ending with /)
            # or /tools/<toolname>/#<anchor> (for sub-packages)
            for a in soup.find_all('a', href=True):
                href = a['href']
                total_links += 1
                
                # Collect sample hrefs for debugging
                if total_links <= 50:
                    sample_hrefs.append(href)
                
                # Match https://www.kali.org/tools/<name>/
                if href.startswith('https://www.kali.org/tools/'):
                    tools_links += 1
                    # Remove any anchor/fragment
                    clean_href = href.split('#')[0]
                    # Ensure it ends with /
                    if clean_href.endswith('/'):
                        # Extract just the path part to validate structure
                        path = clean_href.replace('https://www.kali.org', '')
                        parts = [p for p in path.split('/') if p]
                        # Should be ['tools', '<toolname>']
                        if len(parts) == 2 and parts[0] == 'tools':
                            if clean_href not in urls:
                                urls.append(clean_href)
            
            # Debug output to file - always write, even if successful
            if self.debug_scraper:
                try:
                    import os
                    debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug_scraper.txt')
                    with open(debug_path, 'w', encoding='utf-8') as debug_f:
                        debug_f.write(f"[DEBUG] Total links found: {total_links}\n")
                        debug_f.write(f"[DEBUG] Links with /tools/ in them: {tools_links}\n")
                        debug_f.write(f"[DEBUG] Unique tool URLs extracted: {len(urls)}\n\n")
                        debug_f.write("[DEBUG] Sample of first 50 hrefs:\n")
                        for href in sample_hrefs:
                            debug_f.write(f"  {href}\n")
                        debug_f.write("\n")
                        if len(urls) < 500:
                            debug_f.write("[DEBUG] URLs extracted:\n")
                            for url in urls:
                                debug_f.write(f"  {url}\n")
                    logger.info("Debug scraper log written to %s", debug_path)
                except Exception as ex:
                    logger.debug("Failed to write debug scraper log: %s", ex)
                
        except Exception as e:
            # Log error for debugging but don't crash
            import sys
            print(f"[Warning] Error fetching tool links: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return []
        
        # Return unique URLs (already deduplicated in loop)
        return urls

    def _parse_tool_page_for_package(self, tool_url: str) -> Optional[Tuple[str, Optional[str], List[str]]]:
        """Return (package_name, category, subpackages_list) parsed from a tool page URL.
        
        Subpackages are related packages shown on the tool page (e.g., apache2-bin, apache2-dev for apache2).
        """
        if not WEB_SCRAPING_AVAILABLE or requests is None or BeautifulSoup is None:
            return None
        try:
            if self.discovery_delay:
                time.sleep(self.discovery_delay)
            resp = requests.get(tool_url, timeout=10)
            if resp.status_code != 200:
                return None
            
            # Extract package name from URL as fallback: /tools/toolname/ -> toolname
            pkg_from_url = tool_url.rstrip('/').split('/')[-1]
            
            if parse_tool_page:
                parsed = parse_tool_page(resp.text)
                if parsed:
                    pkg, cat, _ = parsed
                    # External parser doesn't return subpackages, so return empty list
                    return pkg, cat, []
            soup = BeautifulSoup(resp.content, 'html.parser')
            package_candidates: List[str] = []
            subpackages: List[str] = []
            
            # Prefer structured data: look for definition lists <dl><dt>Package</dt><dd>name</dd>
            for dl in soup.find_all('dl'):
                dts = dl.find_all('dt')
                for dt in dts:
                    label = dt.get_text(strip=True).lower()
                    if label in {'package','tool','name'}:
                        dd = dt.find_next('dd')
                        if dd:
                            txt = dd.get_text(strip=True).lower()
                            if re.match(r'^[a-z0-9][a-z0-9+\-.]{2,}$', txt):
                                package_candidates.append(txt)
            pkg = package_candidates[0] if package_candidates else None
            if not pkg:
                # Fallback to textual regex search
                text = soup.get_text('\n', strip=True)
                m = re.search(r'Package\s*:\s*([a-z0-9][a-z0-9+\-.]+)', text, re.IGNORECASE)
                if m:
                    pkg = m.group(1).lower()
            
            # If still no package found, use the tool name from URL
            if not pkg:
                pkg = pkg_from_url
            
            # Extract sub-packages from the page
            # Look for links with href pattern: /tools/<toolname>/#<packagename> (absolute or relative)
            # From the webpage, links look like: https://www.kali.org/tools/apache2/#apache2-bin
            base_path = f"/tools/{pkg_from_url}/#"
            base_path_abs = f"https://www.kali.org/tools/{pkg_from_url}/#"
            
            for a in soup.find_all('a', href=True):
                href = a['href']
                # Check if this is a sub-package link (has anchor pointing to same tool page)
                if base_path in href or base_path_abs in href:
                    # Extract package name from anchor
                    anchor = href.split('#')[-1]
                    # Validate it looks like a package name
                    if anchor and re.match(r'^[a-z0-9][a-z0-9+\-.]{1,}$', anchor):
                        # Only add if it's different from the main package
                        if anchor != pkg and anchor not in subpackages:
                            subpackages.append(anchor)
            
            # Category / tags parsing
            category = None
            tag_values: List[str] = []
            for dl in soup.find_all('dl'):
                for dt in dl.find_all('dt'):
                    label = dt.get_text(strip=True).lower()
                    if any(k in label for k in ( 'category','tags','tag')):
                        dd = dt.find_next('dd')
                        if dd:
                            # Collect all link texts or comma separated tokens
                            links = [a.get_text(strip=True).lower() for a in dd.find_all('a') if a.get_text(strip=True)]
                            if links:
                                tag_values.extend(links)
                            else:
                                raw = dd.get_text(" ", strip=True).lower()
                                tag_values.extend([t.strip() for t in re.split(r'[;,]', raw) if t.strip()])
            # Map first meaningful tag to category set if possible
            mapping = {
                'web':'web','crawler':'web','http':'web','recon':'recon','enumeration':'recon','wireless':'wireless','wifi':'wireless',
                'forensics':'forensics','memory':'forensics','exploitation':'exploitation','exploit':'exploitation','password':'password',
                'cracking':'password','bruteforce':'password','sniffing':'sniffing','capture':'sniffing','reverse':'reverse','phishing':'social',
                'social':'social','database':'database','sql':'database'
            }
            for tag in tag_values:
                for key, cat in mapping.items():
                    if key in tag:
                        category = cat
                        break
                if category:
                    break
            if not category and tag_values:
                category = 'other'
            if not pkg:
                return None
            return pkg, category, subpackages
        except Exception as e:
            import sys
            print(f"  └─ Parse error: {e}", file=sys.stderr)
            return None

    def discover_from_kali_site(self, ttl_hours: int = 168) -> List[str]:
        """Discover and add tools based only on the Kali tools website.

        Returns list of newly added package names.
        """
        # Use cache first
        cache = self._load_kali_site_cache()
        now = time.time()
        urls: List[str] = []
        added: List[str] = []
        if cache and isinstance(cache, dict):
            ts = cache.get('timestamp', 0)
            if (now - ts) < (ttl_hours * 3600):
                urls = cache.get('tool_urls', []) or []
        if not urls:
            urls = self._fetch_kali_tool_links(max_pages=5)
            if urls:
                self._save_kali_site_cache({'timestamp': now, 'tool_urls': urls})

        if not urls:
            return []

        from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
        
        existing = {t.name for t in self.tools}
        lock = Lock()  # Thread-safe lock for shared data
        added: List[str] = []

        def record_parsed(parsed: Optional[Tuple[str, Optional[str], List[str]]]) -> None:
            if not parsed:
                return
            pkg, cat, subpkgs = parsed
            with lock:
                if pkg in existing:
                    return
                tool = Tool(
                    name=pkg,
                    commands=[],
                    installed=self.check_installation(pkg),
                    category=(cat or 'other'),
                    size=0,
                    subpackages=subpkgs or [],
                )
                self.tools.append(tool)
                existing.add(pkg)
                added.append(pkg)
        
        if self.debug_scraper:
            console.print("[cyan]Debug scraper mode: verbose output enabled with concurrency[/cyan]")
            total = len(urls)
            counter = 0
            with ThreadPoolExecutor(max_workers=self.discovery_workers) as executor:
                future_to_meta = {
                    executor.submit(self._parse_tool_page_for_package, url): (idx, url)
                    for idx, url in enumerate(urls, start=1)
                }
                for future in as_completed(future_to_meta):
                    idx, url = future_to_meta[future]
                    try:
                        parsed = future.result()
                        record_parsed(parsed)
                        if parsed:
                            status = f"[green]  ✓ Parsed {parsed[0]}[/green]"
                        else:
                            status = "[yellow]  ⚠️  No package detected[/yellow]"
                    except Exception as exc:
                        status = f"[red]  ✗ Error parsing {url}: {exc}[/red]"
                    finally:
                        with lock:
                            counter += 1
                            order = counter
                    console.print(f"[dim]{idx}/{total} -> {url}")
                    console.print(status)
                    if order % 25 == 0:
                        console.print(f"[cyan]Processed {order}/{total} URLs...[/cyan]")
            console.print(f"[cyan]Debug scrape complete: added {len(added)} tools[/cyan]")
        else:
            completed = 0
            # Knight Rider style progress bar with bright, visible colors
            with Progress(
                TextColumn("[bold bright_red]▓[/bold bright_red] [bold white]{task.description}[/bold white]"),
                BarColumn(bar_width=60, style="bright_black", complete_style="bright_red", pulse_style="bold red on bright_red"),
                TextColumn("[bold yellow]{task.completed}[/bold yellow][white]/[/white][bold cyan]{task.total}[/bold cyan]"),
                TextColumn("[bright_black]│[/bright_black]"),
                TimeElapsedColumn(),
                console=console,
                transient=False
            ) as progress:
                task = progress.add_task(
                    "SCANNING KALI TOOLS", 
                    total=len(urls),
                    completed=0
                )
                
                # Process URLs concurrently with ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=self.discovery_workers) as executor:
                    # Submit all fetch tasks
                    future_to_url = {executor.submit(self._parse_tool_page_for_package, url): url for url in urls}
                    
                    # Process results as they complete
                    for future in as_completed(future_to_url):
                        url = future_to_url[future]
                        
                        try:
                            parsed = future.result()
                            record_parsed(parsed)
                        except Exception as exc:
                            logger.debug("Failed to parse %s: %s", url, exc)
                        finally:
                            with lock:
                                completed += 1
                                progress.update(task, completed=completed)

        console.print(f"[green]✓ Discovery complete: Added {len(added)} new tools (Total: {len(self.tools)})[/green]")
        # Re-categorize known ones based on CATEGORIES mapping
        self._categorize_tools()
        return added

    # Ratings persistence removed

    def get_cached_description(self, package_name: str) -> Optional[str]:
        """Return (and cache) a short description for a package using apt-cache show.

        Handles multi-line Description fields and localized variants (Description-en).
        Limits continuation capture to first 3 indented lines to stay concise.
        """
        cached = self.description_cache.get(package_name)
        if cached is not None:
            return cached
        info = self.get_tool_info(package_name)
        if not info:
            return None
        lines = info.splitlines()
        base: Optional[str] = None
        continuation: List[str] = []
        capturing = False
        for line in lines:
            if line.startswith('Description-en:') or line.startswith('Description:'):
                # Prefer English description if both exist; first wins then we may override if en appears later
                content = line.split(':', 1)[1].strip()
                # If we already captured a non -en and now see -en, replace
                if base is None or line.startswith('Description-en:'):
                    base = content
                    continuation = []
                    capturing = True
                continue
            if capturing:
                if line.startswith(' '):
                    if len(continuation) < 3:
                        continuation.append(line.strip())
                else:
                    break
        if base:
            if continuation:
                base = base + ' ' + ' '.join(continuation)
            self.description_cache[package_name] = base
            return base
        return None

    def show_tool_help(self, tool_name: str) -> bool:
        """Display tool help/usage"""
        tool = next((t for t in self.tools if t['name'] == tool_name), None)
        if not tool or not tool['commands']:
            console.print(f"[yellow]No commands available for {tool_name}[/yellow]")
            return False
        
        command = tool['commands'][0]
        try:
            result = subprocess.run(
                [command, '--help'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.stdout or result.stderr:
                output = result.stdout or result.stderr
                console.print(Panel(
                    Syntax(output[:2000], "text", theme="monokai", line_numbers=False),
                    title=f"📖 {command} --help",
                    border_style="cyan"
                ))
                return True
        except FileNotFoundError:
            console.print(f"[yellow]{command} not found. Tool may not be installed.[/yellow]")
        except Exception as e:
            console.print(f"[red]Error running help command: {e}[/red]")
        
        return False

    def launch_tool(self, command: str) -> bool:
        """Launch a tool in a new terminal"""
        try:
            # Prefer bash if available for a login shell; fallback to /bin/sh
            bash_path = shutil.which('bash')
            shell_cmd = []
            if bash_path:
                shell_cmd = [bash_path, '-lc']
            else:
                sh_path = shutil.which('sh') or '/bin/sh'
                shell_cmd = [sh_path, '-c']

            # Build candidate terminal invocations (most compatible first)
            cmd_str = f"{command}; exec bash" if bash_path else f"{command}"
            candidates = [
                ['x-terminal-emulator', '-e', *shell_cmd, cmd_str],
                ['gnome-terminal', '--', *([bash_path, '-lc'] if bash_path else [shell_cmd[0], shell_cmd[1]]), cmd_str],
                ['konsole', '-e', *shell_cmd, cmd_str],
                ['mate-terminal', '--', *([bash_path, '-lc'] if bash_path else [shell_cmd[0], shell_cmd[1]]), cmd_str],
                ['xfce4-terminal', '--hold', '--command', f"bash -lc '{command}; exec bash'" if bash_path else f"sh -c '{command}'"],
                ['xterm', '-e', *shell_cmd, cmd_str],
                ['terminator', '-x', *shell_cmd, cmd_str],
            ]

            for args in candidates:
                term = args[0]
                if not shutil.which(term):
                    continue
                try:
                    subprocess.Popen(args)
                    console.print(f"[green]✓ Launched {command} in new terminal ({term})[/green]")
                    return True
                except Exception:
                    continue

            console.print("[red]No suitable terminal emulator found[/red]")
            console.print("[dim]Try installing one, e.g.: xterm, gnome-terminal, konsole, xfce4-terminal, terminator[/dim]")
            return False
        except Exception as e:
            console.print(f"[red]Error launching tool: {e}[/red]")
            return False

    # recommend_tools removed

    def create_backup(self) -> bool:
        """Create backup of installed packages"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = Path.home() / f"kali_tools_backup_{timestamp}.txt"
            
            result = subprocess.run(
                ['dpkg', '--get-selections'],
                capture_output=True,
                text=True
            )
            
            with open(backup_file, 'w') as f:
                f.write(result.stdout)
            
            console.print(f"[green]✓ Backup created: {backup_file}[/green]")
            return True
        except Exception as e:
            console.print(f"[red]Error creating backup: {e}[/red]")
            return False

    def setup_local_repo(self, repo_path: str) -> bool:
        """Configure local APT repository"""
        try:
            repo_config = f"deb [trusted=yes] file://{repo_path} ./"
            config_file = "/etc/apt/sources.list.d/local.list"
            
            console.print(f"[yellow]Setting up local repository: {repo_path}[/yellow]")
            console.print(f"[yellow]This will create: {config_file}[/yellow]")
            
            if Confirm.ask("Proceed?"):
                with open('/tmp/local.list', 'w') as f:
                    f.write(repo_config)
                
                subprocess.run(['sudo', 'mv', '/tmp/local.list', config_file])
                subprocess.run(['sudo', 'apt-get', 'update'])
                
                with open(self.local_repo_file, 'w') as f:
                    f.write(repo_path)
                
                console.print("[green]✓ Local repository configured[/green]")
                return True
        except Exception as e:
            console.print(f"[red]Error setting up local repo: {e}[/red]")
        
        return False

    def search_tools(self, query: str) -> List[Dict]:
        """Search tools by name or command"""
        query = query.lower()
        return [
            tool for tool in self.tools
            if query in tool['name'].lower() or
            any(query in cmd.lower() for cmd in tool['commands'])
        ]

    def filter_by_status(self, installed: bool) -> List[Tool]:
        """Filter tools by installation status"""
        return [tool for tool in self.tools if tool.installed == installed]
    
    def filter_by_category(self, category: str) -> List[Tool]:
        """Filter tools by category"""
        return [tool for tool in self.tools if tool.category == category]

    def get_statistics(self) -> Dict:
        """Get statistics about tools"""
        total = len(self.tools)
        installed = sum(1 for tool in self.tools if tool.installed)
        total_size = sum(getattr(tool, 'size', 0) for tool in self.tools if tool.installed)
        
        category_stats = {}
        for category in set(t.category for t in self.tools):
            cat_tools = [t for t in self.tools if t.category == category]
            cat_installed = sum(1 for t in cat_tools if t.installed)
            category_stats[category] = {
                'total': len(cat_tools),
                'installed': cat_installed,
                'percentage': round((cat_installed / len(cat_tools) * 100), 1) if cat_tools else 0
            }
        
        return {
            'total': total,
            'installed': installed,
            'available': total - installed,
            'percentage': round((installed / total * 100), 1) if total > 0 else 0,
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'categories': category_stats
        }

