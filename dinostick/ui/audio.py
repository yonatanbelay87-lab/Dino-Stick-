"""Sound effects, driven by the simulation's event list.

Deliberately forgiving: a machine with no audio device, or a missing asset,
must not take the game down with it. Every failure path here degrades to
silence.

**Nothing here may run on the boot path.** The first ``SoundLoader.load`` is
not a file read -- it opens the audio device and initialises SDL's mixer, and
that was measured at **910 ms** on a desktop with a warm cache. The other four
sounds took 0.5 ms each afterwards. That one call, made from
``GameScreen.__init__``, was the single largest cost in getting to the first
frame, and on a phone it is worse.

So loading happens on a background thread (``warm``) kicked off after the menu
is already on screen, and ``play_events`` never waits for it: if a sound is
wanted while the device is still opening, that one effect is skipped rather
than stalling a frame for the better part of a second.

One caveat, measured, because it is not what "background thread" suggests:
**the load does not fully release the GIL.** While it runs, the Kivy thread
still gets through only about half its usual frames, and anything that needs to
start ANOTHER thread waits a long time behind it -- ``threading.Thread.start()``
for the join worker was measured at 865 ms during the warm-up against 4.8 ms
after it. So this is started as early as possible (immediately after the first
frame) rather than on a delay: it finishes ~2.3 s after launch, which is before
a player can realistically have read the menu and hit a button. Delaying it
would move the contention INTO the window where they are tapping.
"""

from __future__ import annotations

import os
import threading
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

# Serialises actual loads. Two threads opening the mixer at once is not
# something SDL promises to survive, and the warm-up thread holds this for
# most of a second while the device comes up.
_load_lock = threading.Lock()
_warm_thread: threading.Thread | None = None


def warm() -> None:
    """Load every effect on a background thread. Returns immediately.

    Call once, after the first frame is on screen. Until it finishes the game
    is simply silent, which is a far better trade than a second of black
    screen -- and in practice it finishes long before anyone reaches a run.
    """
    global _warm_thread
    if _warm_thread is not None:
        return
    _warm_thread = threading.Thread(target=preload, daemon=True,
                                    name="dinostick-audio-warm")
    _warm_thread.start()


def preload() -> None:
    """Load every effect once. BLOCKS -- see the module docstring.

    Safe to call from a worker thread; never call it from the Kivy thread.
    """
    for filename in sorted(set(EVENT_SOUNDS.values())):
        _get(filename, wait=True)


def _get(filename: str, wait: bool = False):
    """Fetch a loaded sound, loading it if this call is allowed to block.

    ``wait=False`` (the playback path) will not queue behind the warm-up
    thread: if the mixer is still opening, this returns None and that one
    effect is missed. A silent jump is unnoticeable; a frame that took 900 ms
    is not.
    """
    if filename in _cache:
        return _cache[filename]
    if not _load_lock.acquire(blocking=wait):
        return None
    try:
        # Re-check: the thread we just queued behind may have loaded it.
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
    finally:
        _load_lock.release()


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
        # Never blocks: this runs on the Kivy thread, inside a rendered frame.
        sound = _get(filename, wait=False)
        if sound is None:
            continue
        try:
            sound.stop()
            sound.play()
        except Exception:
            pass
