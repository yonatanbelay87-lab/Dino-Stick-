"""Partner rendering: buffer their snapshots, draw them 100 ms in the past.

This is the receiving half of the unreliable path. Each peer gets a small ring
of recent snapshots tagged with the time *we* received them, and every render
frame we ask "where was this dino 100 ms ago?" and interpolate between the two
that bracket the answer.

Two things worth being explicit about.

**The delay is the point, not a cost.** Rendering the newest packet the instant
it lands moves the partner in 20 Hz lurches across a 60 Hz screen. Holding the
view 100 ms back buys a snapshot on each side of every rendered instant, which
is what turns 20 packets a second into continuous motion.

**Spacing comes from the tick, position on the timeline from arrival.** The
obvious first cut is to interpolate purely against arrival times -- it needs no
clock sync at all, which is exactly what you want between two phones that agree
on nothing about wall time. It also has a measurable flaw, and this is what it
looks like: with 20% loss and +-30 ms of jitter, a partner descending at 13
px/frame was rendered descending at 3.9 px/frame for six frames and then
lurched 38 px to catch up.

The cause is that arrival spacing is not send spacing. Two snapshots 100 ms of
*motion* apart can arrive 40 ms apart, and interpolating across the arrival gap
replays that motion at two and a half times speed -- jitter in the network
turned directly into jitter in playback.

The fix needs no clock sync either. Every packet already carries the sender's
world tick, which is a monotonic counter in fixed TICK_DT units, so the *time
between* two snapshots is exactly known: ``(tick_b - tick_a) * TICK_DT``.
Arrival time is then used for one thing only -- a single smoothed offset that
places that sequence on our clock. Jitter lands in the offset, where the
interpolation delay absorbs it, instead of in the playback rate, where it is
visible. Same measurement afterwards: 13.5 px/frame, no lurch.

Note what is still NOT required: that the two devices agree on what time it is.
The offset is derived and self-correcting, and the only clock ever read is ours.

Nothing here touches Kivy or the game; it moves numbers.
"""

from __future__ import annotations

import random
import threading
from collections import deque
from typing import Callable, Final

from game import constants as C
from game import timing

from .statepacket import (EXTRAP_MAX, INTERP_DELAY, PEER_BUFFER, PeerSnapshot,
                          seq_newer, unpack_state)

# How the partner is being drawn right now. Surfaced so the HUD can say so:
# a dino that has quietly stopped updating looks like a dino that has stopped
# playing, and the player deserves to know which it is.
LIVE: Final[str] = "live"        # interpolating between two real snapshots
EXTRAP: Final[str] = "extrapolating"  # guessing forward from the last one
STALE: Final[str] = "stale"      # out of guesses, holding position

# How fast the arrival-time offset tracks the stream. Slow on purpose: this is
# the number jitter is meant to land in, and a fast tracker would pull the
# jitter straight back into the playback rate it was moved out of. At 20 packets
# a second, 0.05 settles a step change in about half a second.
_OFFSET_LERP: Final[float] = 0.05

# An offset disagreement this large is not jitter -- it is a rematch (the tick
# restarts at zero), a device coming back from suspend, or a peer whose clock
# jumped. Ease that and the partner spends seconds somewhere wrong.
_OFFSET_RESYNC: Final[float] = 0.5

# Absorbing a discontinuity. However good the buffer is, a long enough gap in
# the stream eventually has to be caught up on: if a partner's whole takeoff
# was lost, the first packet that arrives afterwards says they are 97 px in the
# air and there is no honest way to have already been there.
#
# What can be chosen is whether that lands in one frame or several. The offset
# between where we were drawing and where the truth turned out to be is kept
# and decayed to zero, so the partner is rendered along the true path from a
# slightly wrong place, converging quickly -- rather than being teleported onto
# it. Real motion is unaffected: in the steady state the offset IS zero, so
# this adds no lag and no smoothing to a stream that is arriving normally.
#
# 0.12 s is about seven frames at 60 fps: fast enough that the partner is never
# meaningfully in the wrong place, slow enough that no single frame reads as a
# jump.
_SMOOTH_TIME: Final[float] = 0.12
# Below this, a step is just motion and smoothing it would be adding lag for
# nothing. A dino at full jump speed covers ~16 px in a frame.
_SMOOTH_TRIGGER: Final[float] = 24.0


class PeerBuffer:
    """The recent history of one remote dino, and how to sample it."""

    __slots__ = ("_snaps", "_seen", "_seen_order", "_last_seq", "_offset",
                 "_error", "_drawn_y", "_sampled_at", "quality", "last_tick")

    def __init__(self) -> None:
        # (play_at, snapshot), oldest first. ``play_at`` is on OUR clock:
        # the sender's tick converted to seconds and shifted by _offset.
        self._snaps: deque = deque(maxlen=PEER_BUFFER)
        # Sequence numbers recently accepted, for duplicate rejection. Bounded,
        # so the 16-bit wrap takes care of itself: a seq cannot come round
        # again inside a window this short.
        self._seen: set[int] = set()
        self._seen_order: deque = deque(maxlen=PEER_BUFFER * 4)
        # Highest seq seen, for "is this the newest?" -- see add().
        self._last_seq: int | None = None
        # Where this peer's tick 0 sits on our clock. None until first heard.
        self._offset: float | None = None
        # Outstanding discontinuity being decayed away -- see _SMOOTH_TIME.
        self._error: float = 0.0
        self._drawn_y: float | None = None
        self._sampled_at: float | None = None
        self.quality: str = STALE
        # The peer's world tick as of its newest snapshot, for world sync.
        self.last_tick: int = 0

    def __len__(self) -> int:
        return len(self._snaps)

    def reset(self) -> None:
        """Between runs: a rematch restarts both the sequence and the tick."""
        self._snaps.clear()
        self._seen.clear()
        self._seen_order.clear()
        self._last_seq = None
        self._offset = None
        self._error = 0.0
        self._drawn_y = None
        self._sampled_at = None
        self.quality = STALE
        self.last_tick = 0

    # -- inbound ------------------------------------------------------------

    def add(self, snap: PeerSnapshot, received_at: float) -> bool:
        """Buffer a snapshot. False if it was a duplicate or arrived too late.

        The obvious rule -- "drop anything whose seq is not newer than the
        newest seen" -- is what a latest-wins renderer wants, and it is wrong
        here. Measured, at 20% loss and +-30 ms of jitter: reordering is
        constant at 50 ms packet spacing, every reordered packet was being
        thrown away on top of the ones the link actually lost, and the
        resulting holes ran 200 ms. One of them swallowed a partner's entire
        takeoff, so the dino sat on the ground for nine frames and then
        appeared 97 px in the air.

        What that rule is really protecting against is rendering backwards, and
        a tick-based timeline gets that for free. A late packet still carries
        the tick it was sent at, so it knows exactly where it belongs; if that
        place is still ahead of what we are drawing, it is useful data and
        dropping it is just throwing away a position we paid for. So:

          * duplicates       rejected (they add nothing)
          * already-rendered rejected (the target has passed them)
          * everything else  inserted at its own place in the timeline,
                             whatever order it turned up in

        The offset is still only ever learned from packets that ARE the newest.
        A late packet's arrival time is late by definition, and letting it move
        the offset would drag the whole timeline back by the size of the delay
        it suffered -- feeding the jitter straight back in.
        """
        if snap.seq in self._seen:
            return False
        elapsed = snap.tick * C.TICK_DT

        is_newest = self._last_seq is None or seq_newer(snap.seq,
                                                        self._last_seq)
        if is_newest:
            self._last_seq = snap.seq
            self.last_tick = snap.tick
            # Where this packet says the peer's tick 0 was, on our clock. Noisy
            # by exactly the amount the network jittered this one packet, which
            # is why it is smoothed rather than used directly.
            candidate = received_at - elapsed
            if (self._offset is None
                    or abs(candidate - self._offset) > _OFFSET_RESYNC):
                self._offset = candidate
                # A rematch, a suspend, or a peer whose clock jumped. The old
                # entries were placed against an offset that no longer applies,
                # and interpolating across that seam would be a visible tear.
                self._snaps.clear()
            else:
                self._offset += (candidate - self._offset) * _OFFSET_LERP
        elif self._offset is None:
            return False  # a late packet before we have any timeline at all

        play_at = self._offset + elapsed
        if self._snaps and play_at <= self._snaps[0][0]:
            return False  # older than everything buffered: already rendered

        self._seen.add(snap.seq)
        self._seen_order.append(snap.seq)
        if len(self._seen_order) == self._seen_order.maxlen:
            # The deque evicts as it appends, so rebuild the set from what
            # survived rather than tracking evictions one at a time.
            self._seen = set(self._seen_order)

        self._insert(play_at, snap)
        return True

    def _insert(self, play_at: float, snap: PeerSnapshot) -> None:
        """Place a snapshot in timeline order. Usually an append.

        ``sample`` walks the deque assuming it is sorted by play_at, so a
        reordered packet has to go where it belongs rather than on the end.
        The scan is backwards because the overwhelmingly common case is "this
        is the newest", which exits on the first comparison.
        """
        if not self._snaps or play_at >= self._snaps[-1][0]:
            self._snaps.append((play_at, snap))
            return
        items = list(self._snaps)
        index = len(items)
        while index > 0 and items[index - 1][0] > play_at:
            index -= 1
        items.insert(index, (play_at, snap))
        # Re-seating the deque keeps maxlen eviction working from the oldest
        # end, which is the end we want to lose.
        self._snaps.clear()
        self._snaps.extend(items[-PEER_BUFFER:])

    # -- outbound -----------------------------------------------------------

    def sample(self, now: float) -> PeerSnapshot | None:
        """Where this dino should be drawn, or None if we have never heard.

        Sets ``quality`` as a side effect so the caller can flag a peer that is
        being guessed at rather than reported.

        Called every render frame, not every packet. That is the whole reason
        20 arrivals a second come out as 60 smooth ones.
        """
        raw = self._sample_raw(now)
        if raw is None:
            return None
        return self._smoothed(raw, now)

    def _sample_raw(self, now: float) -> PeerSnapshot | None:
        snaps = self._snaps
        if not snaps:
            self.quality = STALE
            return None

        target = now - INTERP_DELAY

        # Fresh join, or the buffer only just started filling: the target is
        # older than anything we hold, so there is nothing to interpolate
        # between. Show the oldest we have rather than inventing a position.
        if target <= snaps[0][0]:
            self.quality = LIVE
            return snaps[0][1]

        newest_at, newest = snaps[-1]
        if target > newest_at:
            return self._extrapolate(newest, target - newest_at)

        # Walk backwards: the bracketing pair is almost always the last one,
        # so this is a one-iteration loop in the normal case.
        self.quality = LIVE
        for i in range(len(snaps) - 1, 0, -1):
            older_at, older = snaps[i - 1]
            newer_at, newer = snaps[i]
            if older_at <= target <= newer_at:
                span = newer_at - older_at
                t = 0.0 if span <= 0.0 else (target - older_at) / span
                return _blend(older, newer, t)
        return snaps[0][1]  # unreachable given the guards above

    def _smoothed(self, raw: PeerSnapshot, now: float) -> PeerSnapshot:
        """Absorb a discontinuity into a decaying offset. See _SMOOTH_TIME."""
        dt = 0.0 if self._sampled_at is None else max(0.0, now - self._sampled_at)
        self._sampled_at = now

        # Decay whatever is left of the last correction first, so a new one is
        # measured against where we are actually about to draw.
        if self._error != 0.0 and dt > 0.0:
            self._error *= max(0.0, 1.0 - dt / _SMOOTH_TIME)
            if abs(self._error) < 0.5:
                self._error = 0.0

        if self._drawn_y is not None and dt > 0.0:
            step = (raw.y + self._error) - self._drawn_y
            # How far this dino could plausibly have moved since the last
            # frame, given the speed it is reporting. Anything well past that
            # is a seam in the data, not motion.
            plausible = max(_SMOOTH_TRIGGER, abs(raw.vy) * dt * 2.0)
            if abs(step) > plausible:
                # Keep drawing from where we were and let the truth catch us
                # up, rather than jumping onto it.
                self._error += self._drawn_y - (raw.y + self._error)

        # Never push a dino through the floor: the ground is the one position
        # the player can verify at a glance.
        drawn = max(0.0, raw.y + self._error)
        self._drawn_y = drawn
        if drawn != raw.y:
            return PeerSnapshot(player_id=raw.player_id, seq=raw.seq,
                                tick=raw.tick, x=raw.x, y=drawn, vy=raw.vy,
                                flags=raw.flags)
        return raw

    def _extrapolate(self, last: PeerSnapshot, age: float) -> PeerSnapshot:
        """Dead reckoning: carry the last snapshot forward under gravity.

        Straight-line extrapolation along vy is the textbook answer and it is
        wrong for a dino. Vertical velocity here is never constant for more
        than a tick -- it is a jump arc -- so extending it linearly sends the
        partner shooting off the top of the screen exactly when they are at
        their apex and vy is largest. Integrating gravity instead costs one
        extra multiply and tracks the real arc closely enough that a 250 ms
        gap is not obviously a guess.

        Past EXTRAP_MAX we stop guessing and hold: a wrong position that keeps
        getting wronger has to be corrected by a visible snap, and an honest
        freeze reads as "their connection went" rather than as a bug.
        """
        if age > EXTRAP_MAX:
            age = EXTRAP_MAX
            self.quality = STALE
        else:
            self.quality = EXTRAP

        if last.grounded and last.vy <= 0.0:
            # On the floor and not going anywhere. Nothing to guess.
            return last

        gravity = C.GRAVITY
        if last.want_duck and not last.grounded:
            gravity *= C.DUCK_FALL_MULTIPLIER

        y = last.y + last.vy * age + 0.5 * gravity * age * age
        vy = last.vy + gravity * age
        flags = last.flags
        if y <= 0.0:
            # Landed while we were not looking. Clamping here rather than
            # letting it go negative keeps the dino out of the floor and the
            # rope reading a sane separation.
            y, vy = 0.0, 0.0
            from .statepacket import FLAG_GROUNDED  # local: avoids a cycle
            flags |= FLAG_GROUNDED

        return PeerSnapshot(player_id=last.player_id, seq=last.seq,
                            tick=last.tick, x=last.x, y=y, vy=vy, flags=flags)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _blend(older: PeerSnapshot, newer: PeerSnapshot, t: float) -> PeerSnapshot:
    """Interpolate position and velocity; take flags from whichever is nearer.

    Flags are booleans -- grounded, ducking, dead. There is no halfway house
    between airborne and landed, so they snap at the midpoint instead of being
    blended into something that was never true on either device.
    """
    return PeerSnapshot(
        player_id=newer.player_id,
        seq=newer.seq,
        tick=newer.tick,
        x=_lerp(older.x, newer.x, t),
        y=_lerp(older.y, newer.y, t),
        vy=_lerp(older.vy, newer.vy, t),
        flags=newer.flags if t >= 0.5 else older.flags,
    )


class PeerTable:
    """Every remote dino, keyed by player id."""

    def __init__(self) -> None:
        self._buffers: dict[int, PeerBuffer] = {}
        # When we last accepted a packet from each peer, for the "they have
        # gone quiet" check the rope and the HUD both need.
        self._last_rx: dict[int, float] = {}

    def buffer(self, player_id: int) -> PeerBuffer:
        buf = self._buffers.get(player_id)
        if buf is None:
            buf = PeerBuffer()
            self._buffers[player_id] = buf
        return buf

    def ingest(self, payload: bytes, received_at: float | None = None,
               ignore_id: int | None = None) -> PeerSnapshot | None:
        """Decode and buffer one datagram. Returns it if it was accepted.

        ``ignore_id`` is this device's own player id. The host fans snapshots
        out to every client including, on some LAN setups, back to the sender;
        buffering our own dino would have us render ourselves from the network,
        which is the one thing this whole design exists to avoid.
        """
        snap = unpack_state(payload)
        if snap is None or snap.player_id == ignore_id:
            return None
        when = timing.now() if received_at is None else received_at
        if not self.buffer(snap.player_id).add(snap, when):
            return None
        self._last_rx[snap.player_id] = when
        return snap

    def sample(self, player_id: int, now: float) -> PeerSnapshot | None:
        buf = self._buffers.get(player_id)
        return None if buf is None else buf.sample(now)

    def quality(self, player_id: int) -> str:
        buf = self._buffers.get(player_id)
        return STALE if buf is None else buf.quality

    def silent_for(self, player_id: int, now: float) -> float:
        """Seconds since anything was accepted from this peer."""
        last = self._last_rx.get(player_id)
        return float("inf") if last is None else now - last

    def tick_of(self, player_id: int) -> int:
        buf = self._buffers.get(player_id)
        return 0 if buf is None else buf.last_tick

    def forget(self, player_id: int) -> None:
        self._buffers.pop(player_id, None)
        self._last_rx.pop(player_id, None)

    def reset(self) -> None:
        for buf in self._buffers.values():
            buf.reset()
        self._last_rx.clear()


# ---------------------------------------------------------------------------
# Test instrumentation
# ---------------------------------------------------------------------------


class LossyLink:
    """Wraps a send function to drop, delay and jitter packets on purpose.

    Smooth partner motion on a quiet LAN proves nothing -- the LAN was already
    smooth. This makes a bad connection reproducible on the desk: set the knobs
    in game/constants.py (NET_SIM_*), watch the partner, and the interpolation
    and extrapolation paths are exercised without going near a real network.

    Delayed packets ride a daemon timer thread, which means they arrive on
    *that* thread. That is fine and deliberate: the real receive path is also a
    background thread, so this reproduces the threading as well as the timing.
    """

    def __init__(self, send: Callable[[bytes], None], drop: float = 0.0,
                 latency: float = 0.0, jitter: float = 0.0) -> None:
        self._send = send
        self.drop = drop
        self.latency = latency
        self.jitter = jitter
        self._timers: set[threading.Timer] = set()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.drop > 0.0 or self.latency > 0.0 or self.jitter > 0.0

    def __call__(self, payload: bytes) -> None:
        if self.drop > 0.0 and random.random() < self.drop:
            return
        delay = self.latency + random.uniform(-self.jitter, self.jitter)
        if delay <= 0.0:
            self._send(payload)
            return
        timer = threading.Timer(delay, self._fire, args=(payload,))
        timer.daemon = True
        with self._lock:
            self._timers.add(timer)
        timer.start()

    def _fire(self, payload: bytes) -> None:
        with self._lock:
            self._timers = {t for t in self._timers if t.is_alive()}
        try:
            self._send(payload)
        except OSError:
            pass

    def cancel(self) -> None:
        with self._lock:
            timers, self._timers = self._timers, set()
        for timer in timers:
            timer.cancel()


def wrap_if_simulating(send: Callable[[bytes], None]
                       ) -> Callable[[bytes], None]:
    """Apply the NET_SIM_* knobs, or hand the sender straight back."""
    link = LossyLink(send, drop=C.NET_SIM_DROP, latency=C.NET_SIM_LATENCY,
                     jitter=C.NET_SIM_JITTER)
    return link if link.enabled else send
