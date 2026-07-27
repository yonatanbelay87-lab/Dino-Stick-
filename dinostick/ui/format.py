"""Simulation numbers -> strings a player can read.

The sim is written in pixels because that is what the physics integrates, but
a pixel is a developer's unit: "62,043 px  890 px/s" is a debug readout, not a
score. Everything a human sees goes through here and comes out in metres,
km/h and grouped digits.

One module rather than f-strings scattered through the screens, so the HUD and
the game-over card can never disagree about what a number means.
"""

from __future__ import annotations

from game import constants as C

# Below this many metres, showing kilometres would read "0.06 km" -- all
# leading zero and no sense of progress. Above it, metres stop being legible
# at a glance ("2,430 m" vs "2.43 km").
KM_THRESHOLD = 1000.0


def score(value: float) -> str:
    """Grouped digits, no zero padding.

    The Chrome-dino style "00042" was the reference, but a padded number reads
    as a counter rather than an achievement, and it silently caps the way it
    looks once a good run goes past five digits.
    """
    return f"{int(value):,}"


def metres(pixels: float) -> float:
    return pixels / C.PIXELS_PER_METRE


def distance(pixels: float) -> str:
    """Distance travelled, in metres until it is worth switching to km."""
    m = metres(pixels)
    if m < KM_THRESHOLD:
        return f"{m:,.0f} m"
    return f"{m / 1000.0:,.2f} km"


def speed(pixels_per_second: float) -> str:
    """Scroll speed as km/h -- the one speed unit everybody already owns."""
    return f"{metres(pixels_per_second) * 3.6:,.0f} km/h"


def duration(seconds: float) -> str:
    """m:ss, for run length. Sub-minute runs stay in plain seconds."""
    seconds = max(0.0, seconds)
    if seconds < 60.0:
        return f"{seconds:.0f}s"
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"
