import time
import json
import os
import re
from typing import Dict, Optional
from pathlib import Path

try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

GITHUB_API = "https://api.github.com"
DEFAULT_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "kalitools-cli/0.1"
}

# Basic repo mapping (extend as needed)
REPO_MAP: Dict[str, str] = {
    "nmap": "nmap/nmap",
    "wireshark": "wireshark/wireshark",
    "sqlmap": "sqlmapproject/sqlmap",
    "ffuf": "ffuf/ffuf",
    "hashcat": "hashcat/hashcat",
    "hydra": "vanhauser-thc/thc-hydra",
    "aircrack-ng": "aircrack-ng/aircrack-ng",
    "burpsuite": "portswigger/burp-suite-help" ,  # placeholder, not official
    "metasploit-framework": "rapid7/metasploit-framework",
    "zaproxy": "zaproxy/zaproxy",
    "exploitdb": "offensive-security/exploitdb",
    "wpscan": "wpscanteam/wpscan",
    "dirsearch": "maurosoria/dirsearch",
    "feroxbuster": "epi052/feroxbuster",
    "amass": "owasp-amass/amass",
    "sublist3r": "aboul3la/Sublist3r",
    "masscan": "robertdavidgraham/masscan",
}

class GitHubMetricsCache:
    def __init__(self, path: Path):
        self.path = path
        self.data: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, 'r') as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass

    def get(self, repo: str) -> Optional[Dict]:
        item = self.data.get(repo)
        if not item:
            return None
        # 24h TTL
        if time.time() - item.get('fetched_at', 0) > 86400:
            return None
        return item

    def put(self, repo: str, payload: Dict):
        payload['fetched_at'] = time.time()
        self.data[repo] = payload
        self.save()


def fetch_repo_metrics(repo: str, token: Optional[str] = None) -> Optional[Dict]:
    if not requests:
        return None
    headers = dict(DEFAULT_HEADERS)
    if token:
        headers['Authorization'] = f"Bearer {token}"
    if '/' not in repo:
        return None
    try:
        r = requests.get(f"{GITHUB_API}/repos/{repo}", headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        j = r.json()
        return {
            'stars': j.get('stargazers_count', 0),
            'forks': j.get('forks_count', 0),
            'watchers': j.get('subscribers_count', 0),
            'open_issues': j.get('open_issues_count', 0),
            'last_push': j.get('pushed_at'),
            'created_at': j.get('created_at')
        }
    except Exception:
        return None


def bucketize_github_stars(stars: int) -> int:
    if stars >= 5000: return 5
    if stars >= 1000: return 4
    if stars >= 200: return 3
    if stars >= 50: return 2
    if stars >= 10: return 1
    return 1


def activity_adjustment(last_push: Optional[str]) -> int:
    # ISO timestamp; very rough: <30d +1, >365d -1
    if not last_push:
        return 0
    try:
        import datetime
        ts = datetime.datetime.fromisoformat(last_push.replace('Z','+00:00'))
        delta = datetime.datetime.now(datetime.timezone.utc) - ts
        days = delta.days
        if days <= 30: return 1
        if days >= 365: return -1
        return 0
    except Exception:
        return 0


def age_decay(created_at: Optional[str]) -> int:
    if not created_at:
        return 0
    try:
        import datetime
        ts = datetime.datetime.fromisoformat(created_at.replace('Z','+00:00'))
        age_days = (datetime.datetime.now(datetime.timezone.utc) - ts).days
        if age_days > 365 * 8:
            return -1
        return 0
    except Exception:
        return 0

