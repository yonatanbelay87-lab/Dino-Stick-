"""One clock, used for every elapsed-time measurement in the game.

``time.perf_counter``, never ``time.monotonic``. Both are monotonic and both
look interchangeable, but on Windows (CPython <= 3.12) ``monotonic`` is
GetTickCount64, whose resolution is the OS scheduler tick -- about 15.6 ms.
Measured on this machine it did not advance once across 2000 consecutive
calls, while ``perf_counter`` resolved to 0.1 microseconds.

That granularity is invisible for a six-second timeout and ruinous for
anything at frame scale:

  * Entity interpolation renders at ``now - INTERP_DELAY`` and blends by the
    ratio between two snapshot arrival times. With a 15.6 ms clock, both the
    target and the arrival stamps snap to the same grid, so the blend factor
    jumps in steps instead of sweeping -- a third of rendered frames showed no
    movement at all, which is precisely the stutter interpolation exists to
    remove.
  * A LAN round trip is 1-3 ms. Measured against a 15.6 ms clock it reads as
    either 0 ms or 15.6 ms, and the connection-quality HUD is meaningless.

The epoch is arbitrary and differs from ``monotonic``'s, so the only rule is
consistency: every value that gets compared or subtracted must come from
here.
"""

from __future__ import annotations

from time import perf_counter as now

__all__ = ["now"]
