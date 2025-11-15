# Kali Tools Manager

Terminal UI and automation helpers for browsing, installing, and exporting Kali Linux tooling metadata. **The application targets Kali Linux (or other Debian-based security distributions) exclusively; Windows and macOS are not supported.** The project now ships as a small package (`kalitools/`) with a thin launcher `kalitools.py` so you can either run `python kalitools.py` or `python -m kalitools`.

## Features
- Rich- or basic-mode terminal interface with keyboard navigation, search, categories, utilities menu, and status widgets.
- Tool discovery via kali.org scraper plus meta-package scans with request throttling and caching.
- Installation/uninstallation helpers with sudo verification, dependency display, disk checks, notifications, and dpkg/apt caching.
- Export/import tooling lists, dpkg backups, local repo configuration, and a utilities menu exposed in both UI modes.
- Modular code layout (`kalitools.manager`, `kalitools.ui`, `kalitools.cli`, etc.) with reusable data model and configuration helpers.

## Requirements
Kali Tools Manager targets **Kali Linux and other Debian-based security distributions**. It expects `apt`, `dpkg`, and `sudo` to be available; Windows and macOS are not supported.

Install runtime dependencies inside your Kali shell:

```bash
python3 -m pip install -r requirements.txt
```

> `notify2` is optional and only needed for desktop notifications on Linux.

## Usage
Launch the TUI from a Kali terminal (rich mode will be used automatically on compatible terminals; basic mode remains available for limited consoles):

```bash
python3 kalitools.py --mode auto
```

Inspect command-line options:

```bash
python3 -m kalitools --help
```

## Testing
Lightweight unit tests cover parser defaults, UI auto-detection logic, and model normalization. Run them with the built-in `unittest` runner:

```bash
python3 -m unittest discover tests
```

## Project Layout
```
kalitools.py                # thin launcher that delegates to kalitools.cli
kalitools/                  # package with modular components
  __init__.py               # shared console/logger + logging config helper
  __main__.py               # enables python -m kalitools
  cli.py                    # argparse wiring + entrypoint
  manager.py                # discovery/install/cache/business logic
  ui.py                     # TUI/basics mode implementation
  config.py / constants.py / model.py / notifications.py  # supporting modules
requirements.txt            # runtime dependencies
README.md                   # this file
```

## Additional Documentation

- `docs/GETTING_STARTED.md` – step-by-step environment setup, dependency installation, and test commands.
- `docs/GITHUB_UPLOAD.md` – instructions for initializing git locally and pushing the project to a new GitHub repository.
