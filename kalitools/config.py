"""Utility helpers for exporting/importing Kali tool configurations."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List

from . import console
from .model import Tool


class ConfigManager:
    """Manager for exporting/importing tool configurations."""

    def __init__(self, tools: List[Tool]):
        self.tools = tools

    def export_tools_list(self, filename: str) -> bool:
        """Export installed tools to a JSON file."""
        try:
            installed = [t for t in self.tools if t.installed]
            export_data = {
                'exported_at': datetime.now().isoformat(),
                'total_tools': len(installed),
                'tools': [{'name': t.name, 'commands': t.commands} for t in installed],
            }

            output = Path(filename)
            output.write_text(json.dumps(export_data, indent=2), encoding='utf-8')
            console.print(f"[green]✓ Exported {len(installed)} tools to {output}[/green]")
            return True
        except Exception as exc:  # pragma: no cover - filesystem variability
            console.print(f"[red]Error exporting tools: {exc}[/red]")
            return False

    def import_tools_list(self, filename: str) -> List[str]:
        """Import tool names from a JSON export file."""
        try:
            payload = Path(filename).read_text(encoding='utf-8')
            data = json.loads(payload)
            tool_names = [t['name'] for t in data.get('tools', [])]
            console.print(f"[cyan]Loaded {len(tool_names)} tools from {filename}[/cyan]")
            return tool_names
        except Exception as exc:
            console.print(f"[red]Error importing tools: {exc}[/red]")
            return []
