# Getting Started

## 1. Clone or Download

```bash
# Clone once you have a GitHub remote ready
git clone https://github.com/<your-account>/kalitools.git
cd kalitools
```

If you downloaded a ZIP archive, extract it and `cd` into the resulting directory.

## 2. Install Python 3.10+

The project currently targets modern Python 3 releases. Confirm the version:

```bash
python3 --version
```

If the version is older than 3.10, install a current Python build (`sudo apt install python3 python3-venv python3-pip`).

## 3. Create (Optional) Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 4. Install Dependencies

```bash
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

## 5. Launch the Application

Rich mode is used automatically on capable terminals; basic mode remains available as a fallback.

```bash
python3 kalitools.py --mode auto
```

For CLI usage details:

```bash
python3 -m kalitools --help
```

## 6. Next Steps

- Review `README.md` for feature highlights.
- Browse `kalitools/ui.py` for the interactive key bindings.
- See `docs/GITHUB_UPLOAD.md` when you are ready to publish the repository to GitHub.
- Explore the in-app **Utilities** menu (`Y` key) for:
	- **dpkg backups** – writes `dpkg --get-selections` to a timestamped file so you can restore package selections later.
	- **Local repo configuration** – records the path to an offline/air-gapped repository mirror so installs can source packages without Internet access.
