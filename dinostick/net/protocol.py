"""Wire protocol: message-type constants + framing for both transports.

Two framings, because the two transports have opposite problems:

  TCP  -- a byte stream with no message boundaries. A single recv() can hand
          back half a header, three whole frames, or one and a half, so every
          message carries a 4-byte big-endian length header and all reading
          goes through ``FrameReader``, which buffers until a frame completes.

  UDP  -- datagrams already preserve boundaries, so the length prefix would be
          pure overhead: one JSON object per packet, ``pack``/``unpack``.
          What UDP does not preserve is delivery or order, which is why every
          gameplay packet carries a ``seq`` (see ``SeqFilter``).

Nothing here imports Kivy, and nothing here knows about the game: it moves
dicts.
"""

from __future__ import annotations

import json
import socket
from typing import Any, Iterator

from game import constants as C

# --- Client -> Host, TCP ---------------------------------------------------
MSG_JOIN: str = "join"  # {name, skin}
MSG_READY: str = "ready"  # {ready: bool}
MSG_SKIN: str = "skin"  # {skin: int}

# --- Client -> Host, UDP ---------------------------------------------------
MSG_INPUT: str = "input"  # {seq, jump: bool, duck: bool, tick}
# Sent on game start so the host learns where to aim snapshots. UDP is
# connectionless: the host knows the client's TCP socket, but that tells it
# nothing about which port the client's UDP socket is bound to.
MSG_UDP_REGISTER: str = "udp_register"  # {id}

# --- Host -> Client(s), TCP ------------------------------------------------
MSG_LOBBY: str = "lobby"  # {players: [...], you: id}
MSG_START: str = "start"  # {seed, players: [...]}
MSG_GAMEOVER: str = "gameover"  # {score, distance, cause}
MSG_REMATCH: str = "rematch"  # {}
# One-shot events that must not be lost. Only crashes qualify: a missed jump
# thud is nothing, a missed crash is a death with no sound, no shake and no
# particles. The rest ride along in the snapshot.
MSG_EVENT: str = "event"  # {events: [...]}

# --- Host -> Client(s), UDP ------------------------------------------------
MSG_STATE: str = "state"  # {seq, tick, players, obstacles, ...}

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


class SeqFilter:
    """Drops stale and duplicate datagrams from one sender.

    UDP reorders and duplicates as well as losing packets. Every gameplay
    packet supersedes the one before it, so anything not newer than what we
    already have is simply discarded -- we never wait for a gap to be filled,
    which is the entire point of leaving TCP behind.
    """

    __slots__ = ("last_seq",)

    def __init__(self) -> None:
        self.last_seq = -1

    def accept(self, seq: int) -> bool:
        if seq <= self.last_seq:
            return False
        self.last_seq = seq
        return True

    def reset(self) -> None:
        """Between runs: a rematch restarts sequence numbers from zero."""
        self.last_seq = -1


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


def start(seed: int, players: list[dict[str, Any]]) -> dict[str, Any]:
    return {TYPE: MSG_START, "seed": seed, "players": players}


def player_input(jump: bool, duck: bool, seq: int, tick: int = 0
                 ) -> dict[str, Any]:
    return {TYPE: MSG_INPUT, "jump": jump, "duck": duck, "seq": seq,
            "tick": tick}


def udp_register(player_id: int) -> dict[str, Any]:
    return {TYPE: MSG_UDP_REGISTER, "id": player_id}


def events(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {TYPE: MSG_EVENT, "events": items}


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
