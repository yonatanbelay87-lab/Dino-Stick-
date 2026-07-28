"""The reliable channel: message types + framing for rare, critical events.

The transport is split by how much each message matters, not by convenience.

  TCP, this module -- things that must arrive exactly once and in order:
          HELLO/JOIN, SEED, START, DEATH, REVIVE, POWERUP, SCORE_SYNC,
          GAME_OVER. All low-frequency, all JSON, because a message sent once
          per run does not need a byte-efficient encoding and does need to be
          readable in a packet capture at 2am.

          TCP is a byte stream with no message boundaries: a single recv() can
          hand back half a header, three whole frames, or one and a half. So
          every message carries a 4-byte big-endian length header and all
          reading goes through ``FrameReader``, which buffers until a frame
          completes.

  UDP, net/statepacket.py -- the 20 Hz stream of "here is my dino". Fifteen
          packed bytes, fire and forget, no acknowledgement and no
          retransmission. A lost one is superseded 50 ms later by definition,
          so waiting for it would cost more than losing it.

The split is the design. Put a death on the unreliable stream and someone
occasionally dies with no sound and no particles; put position on the reliable
one and a single dropped packet head-of-line-blocks every position behind it,
which is the stutter that made this game leave TCP in the first place.

Nothing here imports Kivy, and nothing here knows about the game: it moves
dicts.
"""

from __future__ import annotations

import json
import socket
from typing import Any, Iterator

from game import constants as C

# --- Client -> Host, TCP ---------------------------------------------------
MSG_JOIN: str = "join"  # {name, skin}   -- the HELLO of this protocol
MSG_READY: str = "ready"  # {ready: bool}
MSG_SKIN: str = "skin"  # {skin: int}

# --- Client -> Host, UDP ---------------------------------------------------
# Sent on game start so the host learns where to aim the state stream. UDP is
# connectionless: the host knows the client's TCP socket, but that tells it
# nothing about which port the client's UDP socket is bound to.
#
# There is no MSG_INPUT any more. Input never leaves the device it was pressed
# on -- it moves that device's own dino, immediately, and the *result* is what
# goes on the wire. Asking a host for permission to jump is exactly the round
# trip this design exists to delete.
MSG_UDP_REGISTER: str = "udp_register"  # {id}

# --- Host -> Client(s), TCP ------------------------------------------------
MSG_LOBBY: str = "lobby"  # {players: [...], you: id}
MSG_SEED: str = "seed"  # {seed, players: [...]}
MSG_START: str = "start"  # {seed, players: [...], countdown}
MSG_GAMEOVER: str = "gameover"  # {score, distance, cause}
MSG_REMATCH: str = "rematch"  # {}

# --- Shared world events, TCP, either direction ----------------------------
#
# Everything here is rare, one-shot and disastrous to lose. They are also the
# only messages that can change the shared world, which is why they are the
# only ones the host arbitrates: a power-up alters how fast the world scrolls,
# so both devices must apply it on the SAME tick or their obstacle streams
# quietly diverge. The host names that tick.
#
# The claim/grant split is the whole mechanism. A device that touches a
# power-up CLAIMS it (it may have been claimed already, by someone whose packet
# is still in flight); the host answers with a POWERUP naming an apply_tick a
# little way ahead, and every device -- including the host -- applies it there.
MSG_POWERUP_CLAIM: str = "powerup_claim"  # {pid, kind, tick}
MSG_POWERUP: str = "powerup"  # {pid, kind, apply_tick}
# A death is detected locally, on the device that owns the dino, and announced.
# Never inferred from someone else's position: that position is INTERP_DELAY
# old, and killing a partner with it is a death they never experienced.
MSG_DEATH: str = "death"  # {id, tick, obstacle}
MSG_REVIVE: str = "revive"  # {id}
# Whoever's shield ate the hit says so, so the other devices stop counting on
# a shield that is gone.
MSG_SHIELD: str = "shield"  # {id, tick}
# The host restating the shared score once a second. Both devices derive it
# independently from the tick, so this only ever corrects drift.
MSG_SCORE_SYNC: str = "score_sync"  # {tick, score, distance, bonus}

# Degraded path, never the normal one: the 15-byte state packet wrapped in hex
# and sent over TCP. Only reached when the UDP socket could not be bound at all
# -- another program on the port, or a locked-down network. It is a bad way to
# move position (one dropped packet blocks every position behind it, which is
# the whole reason gameplay left TCP) but it is much better than a partner who
# never moves, and it costs one branch on a path that is otherwise dead.
MSG_STATE_TCP: str = "state_tcp"  # {b: hex}

# --- Latency (either direction) --------------------------------------------
# The client stamps a ping with its own clock and the host echoes it back
# verbatim, so round-trip time is measured entirely against one clock and the
# two machines never need their clocks to agree.
MSG_PING: str = "ping"  # {t: float}
MSG_PONG: str = "pong"  # {t: float}

# --- Discovery (UDP) -------------------------------------------------------
MSG_ANNOUNCE: str = "announce"  # {name, game_port, players, max}

TYPE: str = "type"  # every message carries its type under this key


class ProtocolError(Exception):
    """Malformed frame -- the connection should be dropped."""


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


def encode(msg: dict[str, Any]) -> bytes:
    """Serialise a message dict into a complete length-prefixed frame."""
    body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    if len(body) > C.MAX_FRAME_BYTES:
        raise ProtocolError(f"frame too large: {len(body)} bytes")
    return len(body).to_bytes(C.LENGTH_PREFIX_BYTES, "big") + body


def decode(payload: bytes) -> dict[str, Any]:
    """Parse a frame *body* (header already stripped) back into a dict."""
    try:
        msg = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"bad payload: {exc}") from exc
    if not isinstance(msg, dict):
        raise ProtocolError("frame body was not a JSON object")
    return msg


class FrameReader:
    """Reassembles length-prefixed frames from arbitrary chunks of TCP bytes."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> Iterator[dict[str, Any]]:
        """Add received bytes; yield every complete message now available."""
        self._buf.extend(data)
        while True:
            if len(self._buf) < C.LENGTH_PREFIX_BYTES:
                return  # not even a full header yet
            length = int.from_bytes(self._buf[:C.LENGTH_PREFIX_BYTES], "big")
            if length > C.MAX_FRAME_BYTES:
                raise ProtocolError(f"declared frame size {length} is absurd")
            end = C.LENGTH_PREFIX_BYTES + length
            if len(self._buf) < end:
                return  # header known, body still in flight
            payload = bytes(self._buf[C.LENGTH_PREFIX_BYTES:end])
            del self._buf[:end]
            yield decode(payload)


def send(sock: socket.socket, msg: dict[str, Any]) -> None:
    """Send one framed message. Caller holds any needed per-socket lock."""
    sock.sendall(encode(msg))


# ---------------------------------------------------------------------------
# Datagram framing (UDP)
# ---------------------------------------------------------------------------


def pack(msg: dict[str, Any]) -> bytes:
    """Serialise one message into a single datagram. No length prefix."""
    body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    if len(body) > C.MAX_DATAGRAM_BYTES:
        # Fragmenting means losing the whole packet if any fragment drops.
        # Nothing the game sends comes close, so this is a design tripwire.
        raise ProtocolError(f"datagram too large: {len(body)} bytes")
    return body


def unpack(payload: bytes) -> dict[str, Any] | None:
    """Parse one received datagram, or None if it is not one of ours.

    Returns None rather than raising: a UDP socket will happily receive a
    stray broadcast from anything on the subnet, and one malformed packet must
    never take down the receive thread.
    """
    try:
        msg = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return msg if isinstance(msg, dict) else None


# Stale/duplicate rejection for the gameplay stream lives with the packet it
# filters: see ``statepacket.seq_newer`` and ``peers.PeerBuffer.add``. It moved
# there when sequence numbers became 16-bit and started wrapping, because the
# comparison stopped being ``>`` and became something worth testing.


# ---------------------------------------------------------------------------
# Message builders -- typed constructors beat scattered dict literals
# ---------------------------------------------------------------------------


def join(name: str, skin: int) -> dict[str, Any]:
    return {TYPE: MSG_JOIN, "name": name, "skin": skin}


def lobby(players: list[dict[str, Any]], you: int) -> dict[str, Any]:
    return {TYPE: MSG_LOBBY, "players": players, "you": you}


def ready(is_ready: bool) -> dict[str, Any]:
    return {TYPE: MSG_READY, "ready": is_ready}


def skin(index: int) -> dict[str, Any]:
    return {TYPE: MSG_SKIN, "skin": index}


def seed_msg(seed: int, players: list[dict[str, Any]]) -> dict[str, Any]:
    """The shared world, in one integer. Sent before START, never after.

    This is the only thing anyone is ever told about the obstacle course.
    Everything else -- what spawns, where, how fast, how far apart -- both
    devices derive from it with an identical ``random.Random(seed)``.
    """
    return {TYPE: MSG_SEED, "seed": seed, "players": players}


def start(seed: int, players: list[dict[str, Any]], countdown: float
          ) -> dict[str, Any]:
    """Go. ``countdown`` is seconds from *sending* until world tick 0.

    A synchronised start needs both devices to reach tick 0 together, and a
    plain "start now" cannot do that -- it arrives half a round trip late, so
    the joiner is permanently that far behind in a world where the tick number
    IS the obstacle layout. The countdown gives the message time to land; the
    joiner subtracts its measured one-way latency from it (game/coop.py), so
    both clocks reach zero at the same moment rather than the same message.
    """
    return {TYPE: MSG_START, "seed": seed, "players": players,
            "countdown": countdown}


def udp_register(player_id: int) -> dict[str, Any]:
    return {TYPE: MSG_UDP_REGISTER, "id": player_id}


def powerup_claim(pid: int, kind: str, tick: int) -> dict[str, Any]:
    return {TYPE: MSG_POWERUP_CLAIM, "pid": pid, "kind": kind, "tick": tick}


def powerup(pid: int, kind: str, apply_tick: int) -> dict[str, Any]:
    return {TYPE: MSG_POWERUP, "pid": pid, "kind": kind,
            "apply_tick": apply_tick}


def death(player_id: int, tick: int, obstacle: str | None) -> dict[str, Any]:
    return {TYPE: MSG_DEATH, "id": player_id, "tick": tick,
            "obstacle": obstacle}


def revive(player_id: int) -> dict[str, Any]:
    return {TYPE: MSG_REVIVE, "id": player_id}


def shield_break(player_id: int, tick: int) -> dict[str, Any]:
    return {TYPE: MSG_SHIELD, "id": player_id, "tick": tick}


def state_tcp(payload: bytes) -> dict[str, Any]:
    """Wrap a state packet for the reliable channel. See MSG_STATE_TCP."""
    return {TYPE: MSG_STATE_TCP, "b": payload.hex()}


def score_sync(tick: int, score: int, distance: float, bonus: int
               ) -> dict[str, Any]:
    return {TYPE: MSG_SCORE_SYNC, "tick": tick, "score": score,
            "distance": round(distance, 1), "bonus": bonus}


def gameover(score: int, distance: float, player_id: int | None,
             obstacle: str | None) -> dict[str, Any]:
    return {
        TYPE: MSG_GAMEOVER,
        "score": score,
        "distance": distance,
        "cause": {"player_id": player_id, "obstacle": obstacle},
    }


def rematch() -> dict[str, Any]:
    return {TYPE: MSG_REMATCH}


def ping(t: float) -> dict[str, Any]:
    return {TYPE: MSG_PING, "t": t}


def pong(t: float) -> dict[str, Any]:
    return {TYPE: MSG_PONG, "t": t}


def announce(name: str, game_port: int, players: int, max_players: int
             ) -> dict[str, Any]:
    return {
        TYPE: MSG_ANNOUNCE,
        "name": name,
        "game_port": game_port,
        "players": players,
        "max": max_players,
    }
