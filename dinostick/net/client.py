"""TCP game client: sends local input, receives host state into a Queue.

The receive thread never touches Kivy and never touches the renderer -- it
decodes frames and enqueues dicts. The Kivy Clock loop drains ``inbox`` each
frame, keeps the last two state snapshots, and renders an interpolation
between them.
"""

from __future__ import annotations

import queue
import random
import socket
import threading
from typing import Any

from game import constants as C
from game import timing

from . import protocol
from .protocol import FrameReader, ProtocolError  # noqa: F401 -- re-exported


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
        self._last_rx = timing.now()

        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._seq = 0

        # Last input actually sent, so the TCP path only resends on change.
        self._last_input: tuple[bool, bool] | None = None

        # -- gameplay stream (UDP) ------------------------------------------
        self._udp: socket.socket | None = None
        self._udp_thread: threading.Thread | None = None
        self._host_endpoint: tuple[str, int] | None = None
        self._state_seq = protocol.SeqFilter()
        self._last_input_sent = 0.0
        # Registration is a single datagram, and a single datagram can be
        # lost. Until the first snapshot arrives we keep re-announcing
        # ourselves, otherwise one unlucky packet means a frozen world.
        self._registered = False
        self._last_register = 0.0
        # Snapshots land here from the UDP thread; the Kivy loop drains it.
        self.udp_inbox: queue.Queue = queue.Queue()

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

        if C.USE_UDP_GAMEPLAY:
            self._start_udp(ip)

    def _start_udp(self, host_ip: str) -> None:
        """Open the gameplay socket. Best effort -- TCP still carries the game.

        The port is ephemeral: only the host needs a well-known one, and
        binding a fixed port here would stop two clients sharing a machine,
        which is exactly how this gets tested.
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("", 0))
            sock.settimeout(0.5)
        except OSError:
            self._udp = None
            return
        self._udp = sock
        self._host_endpoint = (host_ip, C.PORT_GAME_UDP)
        self._udp_thread = threading.Thread(target=self._udp_loop, daemon=True,
                                            name="dinostick-udp-recv")
        self._udp_thread.start()

    def _udp_loop(self) -> None:
        """Receive snapshots. Own thread, no Kivy, no rendering, no blocking."""
        while not self._stop.is_set():
            sock = self._udp
            if sock is None:
                return
            try:
                data, _addr = sock.recvfrom(2048)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            if (C.SIMULATED_PACKET_LOSS > 0.0
                    and random.random() < C.SIMULATED_PACKET_LOSS):
                continue  # deliberately dropped; see SIMULATED_PACKET_LOSS
            msg = protocol.unpack(data)
            if msg is None or msg.get(protocol.TYPE) != protocol.MSG_STATE:
                continue
            try:
                seq = int(msg.get("seq", 0))
            except (TypeError, ValueError):
                continue
            # Older than what we already have: throw it away rather than
            # rendering backwards. We never wait for a gap to be filled.
            if not self._state_seq.accept(seq):
                continue
            self._registered = True
            self._last_rx = timing.now()
            self.udp_inbox.put(msg)

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
                    if timing.now() - self._last_rx > C.CONNECTION_TIMEOUT:
                        self.error = "The host stopped responding."
                        break
                    continue
                except OSError:
                    break
                if not data:
                    break
                self._last_rx = timing.now()
                try:
                    for msg in reader.feed(data):
                        kind = msg.get(protocol.TYPE)
                        if kind == protocol.MSG_LOBBY:
                            self.player_id = int(msg.get("you", 0))
                        elif kind == protocol.MSG_PONG:
                            # Both timestamps come from OUR clock, so this is a
                            # true RTT with no clock-sync needed. Smoothed so a
                            # single hiccup does not make the readout jump.
                            sample = timing.now() - float(msg.get("t", 0.0))
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
        if self._udp is not None:
            try:
                self._udp.close()
            except OSError:
                pass
            self._udp = None
        if self._udp_thread is not None:
            self._udp_thread.join(timeout=1.0)
            self._udp_thread = None
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
        now = timing.now()
        if now - self._last_ping < C.PING_INTERVAL:
            return
        self._last_ping = now
        self.send(protocol.ping(now))

    def send_input(self, jump: bool, duck: bool, tick: int = 0,
                   force: bool = False) -> None:
        """Send the current input state to the host.

        Over UDP this fires at INPUT_HZ regardless of whether anything
        changed. That looks wasteful next to the TCP path -- which sent one
        message per keypress -- but a packet is 72 bytes and, more to the
        point, an unacknowledged change-only message that gets dropped is a
        jump the host never hears about. Resending is the reliability
        mechanism. It is safe because the host detects a rising edge per tick,
        so a repeated jump:true cannot produce a second hop.
        """
        if self._udp is not None and self._host_endpoint is not None:
            now = timing.now()
            if not force and now - self._last_input_sent < 1.0 / C.INPUT_HZ:
                return
            self._last_input_sent = now
            self._seq += 1
            self._maybe_register(now)
            try:
                self._udp.sendto(
                    protocol.pack(protocol.player_input(jump, duck, self._seq,
                                                        tick)),
                    self._host_endpoint)
            except (OSError, ProtocolError):
                pass  # the next packet is 16ms away; never block the frame
            return

        # TCP fallback: reliable, so only send on change.
        current = (jump, duck)
        if not force and current == self._last_input:
            return
        self._last_input = current
        self._seq += 1
        self.send(protocol.player_input(jump, duck, self._seq, tick))

    def _maybe_register(self, now: float) -> None:
        """Re-announce our UDP endpoint until snapshots start arriving."""
        if self._registered or self.player_id is None:
            return
        if now - self._last_register < C.UDP_REGISTER_INTERVAL:
            return
        self._last_register = now
        try:
            assert self._udp is not None and self._host_endpoint is not None
            self._udp.sendto(protocol.pack(protocol.udp_register(
                self.player_id)), self._host_endpoint)
        except (OSError, ProtocolError, AssertionError):
            pass

    def begin_run(self) -> None:
        """Reset the gameplay stream for a new run.

        Sequence numbers restart at zero on the host, so last run's filter
        would reject this run's first snapshots outright.
        """
        self._state_seq.reset()
        self._registered = False
        self._last_register = 0.0
        self._last_input = None
        self._seq = 0
        while not self.udp_inbox.empty():
            try:
                self.udp_inbox.get_nowait()
            except queue.Empty:
                break
