"""Player preferences that survive a restart: name, dino, last address.

Deliberately tiny and deliberately forgiving. This is a game, not a database:
if the file is missing, unreadable, or written by a future version, every
accessor falls back to a default rather than raising. Losing your preferred
dino is not a reason to fail to start.

Stored under Kivy's ``user_data_dir``, which is the app's private directory on
Android (no storage permission needed) and a per-user config directory on
desktop.
"""

from __future__ import annotations

import json
import os
from typing import Any

FILENAME = "settings.json"

# The host truncates names to 16 characters, so stop the player typing a name
# that will silently come back shorter for everyone else.
MAX_NAME_LENGTH = 16
DEFAULT_NAME = "Player"

_DEFAULTS: dict[str, Any] = {
    "player_name": DEFAULT_NAME,
    "skin": 0,
    "last_address": "",
}

_data: dict[str, Any] = dict(_DEFAULTS)
_path: str | None = None


def clean_name(name: str) -> str:
    """Trim, collapse whitespace, cap the length, never return empty."""
    cleaned = " ".join(str(name).split())[:MAX_NAME_LENGTH].strip()
    return cleaned or DEFAULT_NAME


def load(user_data_dir: str) -> dict[str, Any]:
    """Read the settings file, filling in anything missing with a default."""
    global _path, _data
    _path = os.path.join(user_data_dir, FILENAME)
    _data = dict(_DEFAULTS)
    try:
        with open(_path, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            for key in _DEFAULTS:
                if key in stored:
                    _data[key] = stored[key]
    except (OSError, ValueError):
        pass  # first run, or a file we cannot make sense of
    _data["player_name"] = clean_name(_data["player_name"])
    return _data


def get(key: str, default: Any = None) -> Any:
    return _data.get(key, _DEFAULTS.get(key, default))


def set_value(key: str, value: Any) -> None:
    """Update one setting and persist immediately.

    Written on every change rather than at shutdown: Android kills backgrounded
    apps without ceremony, and ``on_stop`` is not guaranteed to run.
    """
    _data[key] = value
    save()


def save() -> None:
    if _path is None:
        return
    try:
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        with open(_path, "w", encoding="utf-8") as handle:
            json.dump(_data, handle)
    except OSError:
        pass  # read-only storage: play on with in-memory settings
