"""Local font registration. Nothing here ever touches the network.

Kivy resolves ``font_name`` through ``LabelBase``, so a font is registered once
by ROLE -- "Display", "Body", "Mono" -- and every widget asks for the role
rather than a filename. Swapping Fredoka for Baloo is then one line here
instead of a search-and-replace across four screens.

Three rules, in order of importance:

  * No URLs. Not for fonts, not for anything. The game is played on a phone
    hotspot with no internet, and a UI that stalls waiting on a font request
    that will never resolve is worse than an ugly one.
  * A missing font is never fatal. If ``assets/fonts/`` is empty -- which it is
    until you drop the files in -- every role falls back to a font Kivy already
    ships with itself, logs one clear warning, and the game starts.
  * Roles are registered even when they fall back, so ``font_name="Display"``
    is always a valid name and no widget has to guard for absence.

Each role accepts several filenames so you can use whichever of the suggested
families you actually downloaded; the first one present wins.
"""

from __future__ import annotations

import os

from kivy.core.text import LabelBase
from kivy.logger import Logger
from kivy.resources import resource_find

DISPLAY = "Display"
BODY = "Body"
MONO = "Mono"

FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "fonts")

# role -> (accepted filenames in preference order, Kivy's own fallback)
#
# The fallbacks are the fonts shipped inside the Kivy wheel, so they exist on
# every machine and inside every APK without being bundled twice.
ROLES: dict[str, tuple[tuple[str, ...], str]] = {
    DISPLAY: (("Fredoka-Bold.ttf",
               "Fredoka-SemiBold.ttf",
               "Baloo2-Bold.ttf",
               "BalooBhai2-Bold.ttf"),
              "Roboto-Bold.ttf"),
    BODY: (("Nunito-SemiBold.ttf",
            "Nunito-Bold.ttf",
            "Roboto-Medium.ttf"),
           "Roboto-Regular.ttf"),
    MONO: (("SpaceMono-Bold.ttf",
            "SpaceMono-Regular.ttf",
            "JetBrainsMono-Bold.ttf",
            "RobotoMono-Bold.ttf"),
           "RobotoMono-Regular.ttf"),
}

# Filled in by register(); True where the real font was found.
loaded: dict[str, bool] = {}


def _bundled(filename: str) -> str | None:
    """Locate one of Kivy's own fonts, which are always present."""
    found = resource_find(filename) or resource_find(
        os.path.join("data", "fonts", filename))
    if found:
        return found
    # resource_find only searches once the Kivy resource paths are set up,
    # which has not happened if fonts are registered very early. Fall back to
    # the wheel's own directory.
    import kivy  # noqa: PLC0415 -- only needed on this path

    direct = os.path.join(os.path.dirname(kivy.__file__), "data", "fonts",
                          filename)
    return direct if os.path.isfile(direct) else None


# The four magic numbers that open a real sfnt container: TrueType, the
# Apple 'true' variant, an OpenType/CFF font, and a TrueType collection.
_FONT_MAGIC = (b"\x00\x01\x00\x00", b"true", b"OTTO", b"ttcf")


def _looks_like_font(path: str) -> bool:
    """Reject a file that is not actually a font, before registering it.

    ``LabelBase.register`` does not open the file -- it only records the path,
    and the failure surfaces later as a crash the first time something tries to
    draw text in that role. A truncated download or a Git LFS pointer left in
    ``assets/fonts/`` would therefore take the whole UI down at the first Label
    rather than falling back. Four bytes settles it.
    """
    try:
        with open(path, "rb") as handle:
            return handle.read(4) in _FONT_MAGIC
    except OSError:
        return False


def register() -> dict[str, bool]:
    """Register all three roles. Safe to call more than once.

    Returns a role -> "got the real font" map, mostly so a test can assert the
    fallback path works without a font directory.
    """
    global loaded
    loaded = {}
    missing: list[str] = []

    for role, (candidates, fallback) in ROLES.items():
        path = None
        for name in candidates:
            candidate = os.path.join(FONT_DIR, name)
            if not os.path.isfile(candidate):
                continue
            if not _looks_like_font(candidate):
                Logger.warning(f"fonts: {candidate!r} is not a usable font "
                               "file (truncated download?) -- ignoring it")
                continue
            path = candidate
            break

        if path is None:
            path = _bundled(fallback)
            loaded[role] = False
            missing.append(f"{role} (wanted {candidates[0]})")
        else:
            loaded[role] = True

        if path is None:
            # Kivy's own font is unfindable, which should be impossible. Skip
            # the role rather than raising: an unregistered name falls back to
            # the default font at draw time, so the text still appears.
            Logger.warning(f"fonts: no file for role {role!r}, using default")
            continue

        try:
            LabelBase.register(name=role, fn_regular=path)
        except Exception as exc:  # unreadable file, wrong format, ...
            Logger.warning(f"fonts: could not register {role!r} from "
                           f"{path!r}: {exc}")
            loaded[role] = False

    if missing:
        Logger.warning(
            "fonts: falling back to Kivy's built-in Roboto for: "
            + ", ".join(missing))
        Logger.warning(f"fonts: drop the .ttf files into {FONT_DIR} "
                       "(see the README in that folder) to get the real look. "
                       "Nothing is downloaded -- the game runs fine without "
                       "them.")
    return loaded


def report() -> str:
    """One line for the loading screen / logs."""
    if not loaded:
        return "fonts: not registered"
    real = [role for role, ok in loaded.items() if ok]
    fake = [role for role, ok in loaded.items() if not ok]
    parts = []
    if real:
        parts.append("bundled: " + ", ".join(real))
    if fake:
        parts.append("fallback: " + ", ".join(fake))
    return "fonts: " + "; ".join(parts)
