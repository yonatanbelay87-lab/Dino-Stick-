"""Wire protocol: message-type constants + length-prefixed JSON framing.

Framing: a 4-byte big-endian unsigned length header, then that many bytes of
UTF-8 JSON. TCP is a byte stream with no message boundaries -- a single recv()
can hand back half a header, three whole frames, or one and a half -- so all
reading goes through ``FrameReader``, which buffers until a frame is complete.

Nothing here imports Kivy, and nothing here knows about the game: it moves
dicts.
"""

from __future__ import annotations

import json
import socket
from typing import Any, Iterator

from game import constants as C

# --- Client -> Host --------------------------------------------------------
MSG_JOIN: str = "join"  # {name, skin}
MSG_READY: str = "ready"  # {ready: bool}
MSG_SKIN: str = "skin"  # {skin: int}
MSG_INPUT: str = "input"  # {jump: bool, duck: bool, seq: int}

# --- Host -> Client(s) -----------------------------------------------------
MSG_LOBBY: str = "lobby"  # {players: [...], you: id}
MSG_START: str = "start"  # {seed, players: [...]}
MSG_STATE: str = "state"  # {tick, players, obstacles, ...}
MSG_GAMEOVER: str = "gameover"  # {score, distance, cause}
MSG_REMATCH: str = "rematch"  # {}

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


def player_input(jump: bool, duck: bool, seq: int) -> dict[str, Any]:
    return {TYPE: MSG_INPUT, "jump": jump, "duck": duck, "seq": seq}


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
