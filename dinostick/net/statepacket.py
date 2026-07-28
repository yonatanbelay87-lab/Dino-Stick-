"""The gameplay hot path: one dino's state, packed into 15 bytes.

Every device broadcasts *its own* dino at NET_TICK_HZ and nothing else. No
obstacles, no power-ups, no score -- the world is a pure function of the seed
and the tick (see game/world.py), so both devices spawn the same cactus at the
same moment without a byte of it crossing the wire.

Why struct and not the JSON the reliable channel uses: the same six fields come
out at 15 bytes instead of ~110, and the encode happens 20 times a second on
the Kivy thread. JSON is fine for a SEED that is sent once; it is not fine for
the stream.

Everything in here is the *unreliable* half of the transport. A lost packet is
never retransmitted and never waited for -- the next one supersedes it 50 ms
later, which is the entire reason this half is not TCP.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Tunables -- every knob for the unreliable path lives here
# ---------------------------------------------------------------------------

# How often each device sends its own dino, and how often it drains what has
# arrived. Deliberately NOT the render rate: at 60 Hz this is three times the
# packets for motion that interpolation reconstructs anyway. 20 Hz is 50 ms
# between snapshots, which is what sets INTERP_DELAY below.
NET_TICK_HZ: Final[int] = 20
NET_TICK_DT: Final[float] = 1.0 / NET_TICK_HZ

# How far in the past the partner is rendered. This must be comfortably more
# than one send interval or there is routinely no newer snapshot to interpolate
# *toward*, and the partner freezes between packets -- the exact stutter this
# exists to remove.
#
# 100 ms is two send intervals at 20 Hz, so a single dropped packet still
# leaves a bracketing pair. It was 66 ms when snapshots came at 60 Hz (four
# intervals of headroom); the number went UP because the rate went DOWN. See
# NETCODE.md for how to retune it for a worse connection.
INTERP_DELAY: Final[float] = 0.10

# When the stream stalls, how long to keep guessing before giving up and
# holding still. Past this the guess is worth less than an honest freeze: a
# quarter second of ballistic extrapolation is ~1.5 jump arcs, and beyond that
# a wrong guess has to be corrected by a jump big enough to see.
EXTRAP_MAX: Final[float] = 0.25

# Snapshots kept per peer. At NET_TICK_HZ this is 600 ms of history against a
# 100 ms delay, so the bracketing pair survives a long run of drops.
PEER_BUFFER: Final[int] = 12

# Fixed-point scales. Positions to 0.1 px (a dino is 60 px wide, so this is
# well under a rendering pixel) and velocities to 0.5 px/s. Both are packed as
# int16, giving +-3276.7 px and +-16383 px/s -- against a jump peak of ~188 px
# and a launch velocity of 950 px/s, that is roughly 17x headroom.
POS_SCALE: Final[float] = 10.0
VEL_SCALE: Final[float] = 2.0

_INT16_MIN: Final[int] = -32768
_INT16_MAX: Final[int] = 32767

# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------

#   B  msg_type    always MSG_STATE; the discriminator, because discovery and
#                  the odd stray broadcast land on this socket too
#   B  player_id   0 = host, 1..n = joiners
#   H  seq         per-sender, wraps at 65535 (see seq_newer)
#   I  tick        the sender's world tick
#   h  x           position * POS_SCALE
#   h  y           position * POS_SCALE
#   h  vy          velocity * VEL_SCALE, for dead reckoning
#   B  flags       see FLAG_* below
#
# The brief's suggested layout has send_time_ms in the I slot, marked
# "debug/telemetry only; interp uses recv time". Interpolation here does use
# receive time, so that field would have been four dead bytes -- and this game
# has something far more useful to put in them. The world is deterministic from
# (seed, tick), so the tick is what lets a joiner notice its world clock has
# drifted from the host's and ease it back (CoopSession.sync_world_tick).
STATE_FORMAT: Final[str] = "!BBHIhhhB"
STATE_SIZE: Final[int] = struct.calcsize(STATE_FORMAT)  # 15

MSG_STATE: Final[int] = 1

# Intent flags -- what the peer is asking for. Sent because the extrapolator
# needs them: a dino holding duck in mid-air fast-falls at DUCK_FALL_MULTIPLIER
# gravity, and guessing its arc with normal gravity is visibly wrong.
FLAG_JUMP: Final[int] = 1 << 0
FLAG_DUCK: Final[int] = 1 << 1
# Result flags -- what actually happened on the peer's own device, which is
# authoritative for its own dino.
FLAG_DEAD: Final[int] = 1 << 2
FLAG_GROUNDED: Final[int] = 1 << 3
FLAG_DUCKING: Final[int] = 1 << 4

_SEQ_MASK: Final[int] = 0xFFFF
_SEQ_HALF: Final[int] = 0x8000
_TICK_MASK: Final[int] = 0xFFFFFFFF


def seq_newer(candidate: int, current: int) -> bool:
    """Is ``candidate`` newer than ``current`` in a 16-bit wrapping sequence?

    Plain ``>`` is wrong at the wrap: seq 0 follows seq 65535, and a naive
    comparison rejects every packet for the rest of the run once the counter
    rolls over. At 20 Hz that is a game-ending bug 54 minutes in -- rare enough
    to ship, certain enough to happen.

    The rule is the standard one: treat the difference as signed over half the
    space. Anything within 32767 ahead is newer, anything behind is stale.
    """
    return ((candidate - current) & _SEQ_MASK) != 0 and \
           ((candidate - current) & _SEQ_MASK) < _SEQ_HALF


def _q(value: float, scale: float) -> int:
    """Quantise to int16, clamping rather than letting struct raise."""
    return max(_INT16_MIN, min(_INT16_MAX, int(round(value * scale))))


@dataclass(slots=True)
class PeerSnapshot:
    """One dino's state as it left the peer's device, decoded."""

    player_id: int
    seq: int
    tick: int
    x: float
    y: float
    vy: float
    flags: int

    @property
    def alive(self) -> bool:
        return not self.flags & FLAG_DEAD

    @property
    def grounded(self) -> bool:
        return bool(self.flags & FLAG_GROUNDED)

    @property
    def ducking(self) -> bool:
        return bool(self.flags & FLAG_DUCKING)

    @property
    def want_duck(self) -> bool:
        return bool(self.flags & FLAG_DUCK)


def flags_for(want_jump: bool, want_duck: bool, alive: bool, grounded: bool,
              ducking: bool) -> int:
    flags = 0
    if want_jump:
        flags |= FLAG_JUMP
    if want_duck:
        flags |= FLAG_DUCK
    if not alive:
        flags |= FLAG_DEAD
    if grounded:
        flags |= FLAG_GROUNDED
    if ducking:
        flags |= FLAG_DUCKING
    return flags


def pack_state(player_id: int, seq: int, tick: int, x: float, y: float,
               vy: float, flags: int) -> bytes:
    """Build one STATE datagram. Never raises on out-of-range input."""
    return struct.pack(
        STATE_FORMAT,
        MSG_STATE,
        player_id & 0xFF,
        seq & _SEQ_MASK,
        tick & _TICK_MASK,
        _q(x, POS_SCALE),
        _q(y, POS_SCALE),
        _q(vy, VEL_SCALE),
        flags & 0xFF,
    )


def unpack_state(payload: bytes) -> PeerSnapshot | None:
    """Decode a STATE datagram, or None if this is not one.

    Returns None rather than raising for the same reason ``protocol.unpack``
    does: a UDP socket receives whatever the subnet feels like sending it, and
    one malformed packet must never take down the receive thread.
    """
    if len(payload) != STATE_SIZE or payload[0] != MSG_STATE:
        return None
    try:
        (_kind, player_id, seq, tick, x, y, vy,
         flags) = struct.unpack(STATE_FORMAT, payload)
    except struct.error:
        return None
    return PeerSnapshot(
        player_id=player_id,
        seq=seq,
        tick=tick,
        x=x / POS_SCALE,
        y=y / POS_SCALE,
        vy=vy / VEL_SCALE,
        flags=flags,
    )
