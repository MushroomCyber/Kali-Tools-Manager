import json
from pathlib import Path
from typing import Any, Dict, Optional


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            with open(path, 'r') as f:
                return json.load(f)
    except Exception:
        return None
    return None


def save_json(path: Path, data: Dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


class RatingCache:
    def __init__(self, path: Path):
        self.path = path
        self.data: Dict[str, int] = {}
        self.load()

    def load(self):
        obj = load_json(self.path)
        if isinstance(obj, dict):
            self.data = {k: int(v) for k, v in obj.items() if isinstance(v, (int, float))}

    def save(self) -> bool:
        return save_json(self.path, self.data)

    def get(self, key: str) -> Optional[int]:
        return self.data.get(key)

    def put(self, key: str, value: int):
        try:
            self.data[key] = int(value)
        except Exception:
            pass
