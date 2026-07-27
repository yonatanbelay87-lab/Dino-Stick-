"""Sound effects, driven by the simulation's event list.

Deliberately forgiving: a machine with no audio device, or a missing asset,
must not take the game down with it. Every failure path here degrades to
silence.
"""

from __future__ import annotations

import os
from typing import Any

from kivy.core.audio import SoundLoader

from game import constants as C

_SOUND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "assets", "sounds")

# Simulation event name -> sound file.
EVENT_SOUNDS: dict[str, str] = {
    "jump": "jump.wav",
    "land": "land.wav",
    "crash": "crash.wav",
    "powerup": "powerup.wav",
    "shield_break": "shield.wav",
}

_cache: dict[str, Any] = {}
_enabled = True


def preload() -> None:
    """Load every effect once, up front, so the first jump is not silent."""
    for filename in set(EVENT_SOUNDS.values()):
        _get(filename)


def _get(filename: str):
    if filename in _cache:
        return _cache[filename]
    sound = None
    try:
        path = os.path.join(_SOUND_DIR, filename)
        if os.path.exists(path):
            sound = SoundLoader.load(path)
            if sound is not None:
                sound.volume = C.SOUND_VOLUME
    except Exception:
        sound = None  # no audio device, unsupported format, ...
    _cache[filename] = sound
    return sound


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = on


def play_events(events: list[dict]) -> None:
    """Play one sound per distinct event kind in this batch.

    Deduplicated by kind: four dinos landing on the same tick should be one
    thud, not four stacked on top of each other.
    """
    if not _enabled or not events:
        return
    for kind in {e.get("e") for e in events}:
        filename = EVENT_SOUNDS.get(kind or "")
        if not filename:
            continue
        sound = _get(filename)
        if sound is None:
            continue
        try:
            sound.stop()
            sound.play()
        except Exception:
            pass
