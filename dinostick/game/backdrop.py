"""The scrolling season backdrop: where the art goes, at any given distance.

The whole background is one endless horizontal STRIP, laid out once and read
from at whatever point the team has reached:

    [ season 1 x N ][ 1T2 ][ season 2 x N ][ 2T3 ][ ... ][ 8T1 ] -> wraps

A season is tiled edge to edge for 500 m; a transition is laid down as a
single copy, so it sweeps past exactly once on its way into the next season
and cannot come round again until the whole cycle wraps. Nothing fades or
cuts: the transition art already blends one season into the next, so simply
scrolling through it *is* the transition.

Two decisions carry most of the weight here:

*   **Distance in, layout out.** ``tiles()`` is a pure function of how far the
    team has run -- there is no scroll accumulator to drift, nothing to reset
    between runs, and no state to put on the wire. That is what makes the
    progression identical in local, host and client mode for free: all three
    already know ``state.distance``, so all three land on the same season at
    the same metre mark. It also means a mid-run resize simply re-lays the
    strip at the correct place instead of tearing.

*   **Seasons snap to whole copies.** The number of copies in a season is
    rounded so the last one ends exactly on the 500 m mark, and the effective
    parallax is nudged by a few percent to pay for it. Spacing the copies at
    an exact BG_PARALLAX instead leaves the season cut off mid-image where the
    transition starts, which reads as a tear across the sky.

Loading is asynchronous (Kivy's Loader), lazy, and forgiving: a missing or
still-decoding image just isn't in the returned list, and the renderer falls
back to the old hills for that frame rather than dropping to a blank sky.
"""

from __future__ import annotations

import os
from typing import Any

from kivy.loader import Loader

from . import constants as C

_BG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", C.BG_DIR_NAME)


class Segment:
    """One entry in the cycle: an image and the stretch of run it covers."""

    __slots__ = ("filename", "transition", "metres", "start")

    def __init__(self, filename: str, transition: bool, metres: float,
                 start: float) -> None:
        self.filename = filename
        self.transition = transition
        self.metres = metres  # length of this segment, in metres of running
        self.start = start  # metres into the cycle where it begins


def _build_cycle() -> tuple[tuple[Segment, ...], float]:
    segments: list[Segment] = []
    start = 0.0
    for filename, transition in C.BG_SEQUENCE:
        metres = C.BG_TRANSITION_METRES if transition else C.BG_SEASON_METRES
        segments.append(Segment(filename, transition, metres, start))
        start += metres
    return tuple(segments), start


SEGMENTS, CYCLE_METRES = _build_cycle()


def segment_at(metres: float) -> tuple[int, float]:
    """(index, 0..1 progress) for a distance, wrapping around the cycle."""
    if CYCLE_METRES <= 0.0:  # defensive: empty sequence
        return 0, 0.0
    position = metres % CYCLE_METRES
    for index, segment in enumerate(SEGMENTS):
        if position < segment.start + segment.metres:
            return index, (position - segment.start) / segment.metres
    return len(SEGMENTS) - 1, 0.0  # unreachable while the table is consistent


class Backdrop:
    """Texture cache plus the strip layout, for one view."""

    def __init__(self) -> None:
        # filename -> Kivy ProxyImage, in least-recently-used order.
        self._proxies: dict[str, Any] = {}
        self._missing: set[str] = set()
        self._tuned: set[str] = set()

    # -- assets -------------------------------------------------------------

    def warm(self, metres: float = 0.0) -> None:
        """Start decoding what a run beginning here will need first.

        Called well before the run starts (the screen is built at launch), so
        the first frame already has its season instead of a flash of hills.
        """
        index, _ = segment_at(metres)
        for step in (0, 1):
            self._texture(SEGMENTS[(index + step) % len(SEGMENTS)].filename)

    def _texture(self, filename: str):
        """The texture for one image, or None while it loads / if it is gone.

        Requesting is what schedules the load, so this doubles as the prefetch
        call. Touching an entry also marks it most-recently-used.
        """
        if filename in self._missing:
            return None

        proxy = self._proxies.pop(filename, None)
        if proxy is None:
            path = os.path.join(_BG_DIR, filename)
            if not os.path.exists(path):
                self._missing.add(filename)  # never ask for it again
                return None
            try:
                proxy = Loader.image(path)
            except Exception:
                self._missing.add(filename)
                return None
        # Re-inserting moves it to the end: plain dict order is the LRU.
        self._proxies[filename] = proxy
        while len(self._proxies) > C.BG_CACHE:
            self._proxies.pop(next(iter(self._proxies)))

        if not getattr(proxy, "loaded", False):
            return None
        texture = proxy.texture
        if texture is None or not texture.height:
            return None
        # Loader hands back its own "loading"/"error" placeholder on failure.
        # Those are roughly square; every background is 3:1 or wider.
        if texture.width < texture.height * C.BG_MIN_ASPECT:
            return None

        if C.BG_PIXEL_ART and filename not in self._tuned:
            # Set once, not per frame: each assignment rebinds the texture.
            texture.mag_filter = "nearest"  # crisp pixels when blown up
            texture.min_filter = "linear"  # but no aliasing on a small window
            self._tuned.add(filename)
        return texture

    # -- layout -------------------------------------------------------------

    def _strip(self, segment: Segment, height: float
               ) -> tuple[Any, float, int]:
        """(texture, drawn width of one copy, number of copies) for a segment.

        The texture comes back with it because the width depends on the aspect
        ratio, which is only known once the image has decoded. Until then the
        segment is laid out at a nominal aspect and simply not drawn, so the
        strip does not shuffle sideways when the real one arrives.
        """
        texture = self._texture(segment.filename)
        aspect = (texture.width / texture.height if texture is not None
                  else C.BG_FALLBACK_ASPECT)
        width = max(1.0, height * aspect)
        if segment.transition:
            return texture, width, 1  # exactly one pass, by definition
        # Round to whole copies so the season ends flush with the transition.
        target = C.BG_SEASON_METRES * C.PIXELS_PER_METRE * C.BG_PARALLAX
        return texture, width, max(1, int(round(target / width)))

    def tiles(self, metres: float, left: float, right: float, view_top: float
              ) -> list[tuple[Any, float, float, float, float]]:
        """Everything to draw right now, as (texture, x, y, w, h).

        Coordinates are design space, with y measured up from the ground line,
        ready for ``Renderer._place``. ``left``/``right`` are the design-x
        range the widget shows and ``view_top`` its top edge in the same units.
        """
        ground = C.BG_GROUND_FRACTION
        if not SEGMENTS or ground <= 0.0 or ground >= 1.0:
            return []

        # One height for every image, so the world does not zoom between
        # seasons. Tall enough that the shallowest-ground image still reaches
        # the bottom edge, and that the deepest-sky one still reaches the top
        # of a window taller than the design space.
        height = max(C.GROUND_Y / ground,
                     (view_top + C.GROUND_Y) / (1.0 - C.BG_GROUND_FRACTION_MAX))

        index, progress = segment_at(metres)
        _, width, copies = self._strip(SEGMENTS[index], height)
        # Design-x of the current segment's left edge. Everything else follows
        # from walking right until we run off the screen.
        x = left - progress * width * copies

        out: list[tuple[Any, float, float, float, float]] = []
        for _ in range(len(SEGMENTS) + 1):  # a full cycle is more than enough
            if x >= right:
                break
            segment = SEGMENTS[index]
            texture, width, copies = self._strip(segment, height)
            if texture is not None:
                # Drop this image so its own painted ground line sits on the
                # ground line -- but never so far up that its bottom edge
                # climbs above the bottom of the screen, which would leave a
                # strip of bare background showing beneath the scenery.
                y = -max(C.BG_GROUND_FRACTIONS.get(segment.filename, ground)
                         * height, C.GROUND_Y)
                # Skip straight to the first copy on screen: a season is a
                # dozen copies long and all but one or two are off to the left.
                first = max(0, int((left - x) // width))
                for copy in range(first, copies):
                    tile_x = x + copy * width
                    if tile_x >= right:
                        break
                    out.append((texture, tile_x, y, width, height))
            x += width * copies
            index = (index + 1) % len(SEGMENTS)

        # Start the next one decoding while there is still time for it.
        ahead, _ = segment_at(metres + C.BG_PREFETCH_METRES)
        self._texture(SEGMENTS[ahead].filename)
        return out
