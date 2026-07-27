"""TCP game client: sends local input, receives host state into a Queue.

The receive thread never touches Kivy and never touches the renderer -- it
decodes frames and enqueues dicts. The Kivy Clock loop drains ``inbox`` each
frame, keeps the last two state snapshots, and renders an interpolation
between them.
"""

from __future__ import annotations

import queue
import socket
import threading
import time
from typing import Any

from game import constants as C

from . import protocol
from .protocol import FrameReader, ProtocolError


class GameClient:
    """One connection to a host."""

    def __init__(self, name: str = "Player", skin: int = 1) -> None:
        self.inbox: queue.Queue = queue.Queue()
        self.name = name
        self.skin = skin

        self.player_id: int | None = None
        self.connected = False
        self.error: str | None = None

        # Round-trip time in seconds, smoothed. None until the first pong.
        self.rtt: float | None = None
        self._last_ping = 0.0
        # Last time anything arrived from the host. The host answers every
        # ping, so this ticks over once a second in every screen.
        self._last_rx = time.monotonic()

        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._seq = 0

        # Last input actually sent, so we only resend on change.
        self._last_input: tuple[bool, bool] | None = None

    # -- lifecycle ----------------------------------------------------------

    def connect(self, ip: str, port: int = C.PORT_GAME) -> None:
        """Blocking connect. Raises OSError if the host is unreachable."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(C.SOCKET_TIMEOUT)
        sock.connect((ip, port))
        sock.settimeout(1.0)
        self._sock = sock
        self.connected = True
        self.error = None
        self._stop.clear()

        self._thread = threading.Thread(target=self._recv_loop, daemon=True,
                                        name="dinostick-client-recv")
        self._thread.start()
        self.send(protocol.join(self.name, self.skin))

    def _recv_loop(self) -> None:
        reader = FrameReader()
        try:
            while not self._stop.is_set():
                try:
                    assert self._sock is not None
                    data = self._sock.recv(8192)
                except (socket.timeout, TimeoutError):
                    # The host going out of range does not close the socket --
                    # it just stops answering. Give up rather than sitting on a
                    # frozen world forever.
                    if time.monotonic() - self._last_rx > C.CONNECTION_TIMEOUT:
                        self.error = "The host stopped responding."
                        break
                    continue
                except OSError:
                    break
                if not data:
                    break
                self._last_rx = time.monotonic()
                try:
                    for msg in reader.feed(data):
                        kind = msg.get(protocol.TYPE)
                        if kind == protocol.MSG_LOBBY:
                            self.player_id = int(msg.get("you", 0))
                        elif kind == protocol.MSG_PONG:
                            # Both timestamps come from OUR clock, so this is a
                            # true RTT with no clock-sync needed. Smoothed so a
                            # single hiccup does not make the readout jump.
                            sample = time.monotonic() - float(msg.get("t", 0.0))
                            self.rtt = (sample if self.rtt is None
                                        else self.rtt * 0.7 + sample * 0.3)
                            continue  # never surfaced to the game layer
                        self.inbox.put(msg)
                except ProtocolError:
                    break
        finally:
            self.connected = False
            self.inbox.put({protocol.TYPE: "_disconnected"})

    def disconnect(self) -> None:
        self._stop.set()
        self.connected = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    # -- sending ------------------------------------------------------------

    def send(self, msg: dict[str, Any]) -> None:
        if self._sock is None or not self.connected:
            return
        try:
            with self._send_lock:
                protocol.send(self._sock, msg)
        except (OSError, ProtocolError) as exc:
            self.error = str(exc)
            self.connected = False

    def maybe_ping(self) -> None:
        """Send a latency probe at most once per PING_INTERVAL.

        Called from every screen, not just in game: the reply is what keeps
        both ends' keepalive clocks fresh, and a lobby is exactly where you sit
        long enough for a phone to wander off the network.
        """
        now = time.monotonic()
        if now - self._last_ping < C.PING_INTERVAL:
            return
        self._last_ping = now
        self.send(protocol.ping(now))

    def send_input(self, jump: bool, duck: bool, force: bool = False) -> None:
        """Send input, but only when it actually changed.

        Holding a key produces one message, not sixty a second. The host keeps
        the last value it received, so there is nothing to keep alive.
        """
        current = (jump, duck)
        if not force and current == self._last_input:
            return
        self._last_input = current
        self._seq += 1
        self.send(protocol.player_input(jump, duck, self._seq))
