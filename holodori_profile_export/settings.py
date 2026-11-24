"""Remembered app settings (region, save-to folder, game data folder) between runs.

Stored as a small JSON file in the usual per-user settings folder for each OS. It never holds a
sign-in, token or any account data. Reading or writing it never stops the app: on any problem the
app just starts with its defaults.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_APP = "holodori-exporter"


def path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / _APP / "settings.json"


def load() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(**values) -> None:
    """Merge the given values into the saved settings (a value of None removes that key)."""
    try:
        data = load()
        for k, v in values.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass
