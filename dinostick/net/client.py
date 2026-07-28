"""The joiner's transport. A TCP control channel and a UDP state stream.

Symmetrical with the host in every way that matters: this device simulates its
own dino, sends the result 20 times a second, and receives everyone else's. It
never sends an input and never waits for a reply before moving. The word
"client" survives only because somebody has to dial and somebody has to answer.

Neither receive thread touches Kivy, the renderer, or the game. The TCP thread
decodes frames into a Queue; the UDP thread appends raw 15-byte payloads and
their arrival time to a deque. The Kivy Clock drains both -- reliable messages
every frame, state packets at NET_TICK_HZ.
"""

from __future__ import annotations

import queue
import selectors
import socket
import threading
from collections import deque
from typing import Any

from game import constants as C
from game import timing

from . import protocol
from .protocol import FrameReader, ProtocolError  # noqa: F401 -- re-exported
from .statepacket import MSG_STATE, STATE_SIZE

# How long the UDP thread parks on the selector before rechecking the stop
# flag. The socket is non-blocking; this is what keeps the thread off the CPU.
_SELECT_TIMEOUT = 0.5


class GameClient:
    """One connection to a host."""

    def __init__(self, name: str = "Player", skin: int = 1) -> None:
        self.inbox: queue.Queue = queue.Queue()
        # Raw state datagrams: (payload, received_at). Stamped here, on
        # arrival, with our own clock -- never with the sender's. Two phones
        # agree on nothing about wall time, and interpolating against arrival
        # times means they never have to (see net/peers.py).
        self.state_inbox: deque = deque(maxlen=256)

        self.name = name
        self.skin = skin

        self.player_id: int | None = None
        self.connected = False
        self.error: str | None = None
        # True from the moment connect_async is called until the socket is up
        # or has failed. The lobby reads it to say "Connecting..." instead of
        # showing controls that cannot work yet.
        self.connecting = False

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

        # -- gameplay stream (UDP) ------------------------------------------
        self._udp: socket.socket | None = None
        self._udp_thread: threading.Thread | None = None
        self._host_endpoint: tuple[str, int] | None = None
        # Registration is a single datagram, and a single datagram can be
        # lost. Until the first state packet arrives we keep re-announcing
        # ourselves, otherwise one unlucky packet means a partner who never
        # moves.
        self._registered = False
        self._last_register = 0.0

    # -- lifecycle ----------------------------------------------------------

    def connect_async(self, ip: str, port: int = C.PORT_GAME) -> None:
        """Connect on a worker thread. Returns immediately, never raises.

        The blocking version below can sit on ``sock.connect`` for the whole
        of SOCKET_TIMEOUT -- five seconds -- and it used to be called straight
        from the join button. During those five seconds the Kivy thread is
        stopped dead: the lobby is already the current screen, so the player
        sees it and taps Ready, and nothing happens because no touch is being
        dispatched at all. Every one of those taps is then delivered in a burst
        when the socket finally answers. That is the "frozen Ready button".

        The result is reported the same way every other socket thread reports
        things -- as a message on ``inbox``, drained by the app's pump on the
        main thread. Nothing here touches a widget.
        """
        if self.connecting or self.connected:
            return
        self.connecting = True
        self.error = None
        threading.Thread(target=self._connect_worker, args=(ip, port),
                         daemon=True, name="dinostick-connect").start()

    def _connect_worker(self, ip: str, port: int) -> None:
        try:
            self.connect(ip, port)
        except OSError as exc:
            self.connecting = False
            self.error = str(exc)
            self.inbox.put({protocol.TYPE: "_connect_failed",
                            "error": str(exc)})
            return
        self.connecting = False
        self.inbox.put({protocol.TYPE: "_connected"})

    def connect(self, ip: str, port: int = C.PORT_GAME) -> None:
        """Blocking connect. Raises OSError if the host is unreachable.

        Do not call this from the Kivy thread -- use ``connect_async``. It is
        kept public and blocking because that is the right shape for a worker
        thread and for the headless self-test.
        """
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

        self._start_udp(ip)

    def _start_udp(self, host_ip: str) -> None:
        """Open the gameplay socket. Best effort -- see MSG_STATE_TCP.

        The port is ephemeral: only the host needs a well-known one, and
        binding a fixed port here would stop two clients sharing a machine,
        which is exactly how this gets tested.
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("", 0))
            sock.setblocking(False)
        except OSError:
            self._udp = None
            return
        self._udp = sock
        self._host_endpoint = (host_ip, C.PORT_GAME_UDP)
        self._udp_thread = threading.Thread(target=self._udp_loop, daemon=True,
                                            name="dinostick-udp-recv")
        self._udp_thread.start()

    def _udp_loop(self) -> None:
        """Receive state packets. Own thread, non-blocking socket, no decode.

        The packet is not parsed here and the seq is not checked here. Both
        happen on the Kivy thread when the buffer is drained, which keeps this
        loop to "append two values to a deque" -- there is nothing in it that
        can throw, and nothing that can take long enough to make the next
        packet wait.
        """
        sock = self._udp
        if sock is None:
            return
        selector = selectors.DefaultSelector()
        try:
            selector.register(sock, selectors.EVENT_READ)
        except (OSError, ValueError):
            return
        try:
            while not self._stop.is_set():
                if not selector.select(_SELECT_TIMEOUT):
                    continue
                while True:
                    try:
                        data, _addr = sock.recvfrom(2048)
                    except BlockingIOError:
                        break
                    except OSError:
                        return
                    if len(data) != STATE_SIZE or data[0] != MSG_STATE:
                        continue  # not ours: a stray broadcast on the subnet
                    self._registered = True
                    self._last_rx = timing.now()
                    self.state_inbox.append((data, self._last_rx))
        finally:
            selector.close()

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
                            # true RTT with no clock sync needed. Smoothed so a
                            # single hiccup does not make the readout jump.
                            sample = timing.now() - float(msg.get("t", 0.0))
                            self.rtt = (sample if self.rtt is None
                                        else self.rtt * 0.7 + sample * 0.3)
                            continue  # never surfaced to the game layer
                        elif kind == protocol.MSG_STATE_TCP:
                            # UDP was unavailable at one end. Route it to the
                            # same buffer the datagrams would have landed in,
                            # so nothing downstream has to know.
                            try:
                                payload = bytes.fromhex(str(msg.get("b", "")))
                            except ValueError:
                                continue
                            self.state_inbox.append((payload, timing.now()))
                            continue
                        self.inbox.put(msg)
                except ProtocolError:
                    break
        finally:
            self.connected = False
            self.inbox.put({protocol.TYPE: "_disconnected"})

    def disconnect(self) -> None:
        self._stop.set()
        self.connected = False
        self.connecting = False
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
        self.state_inbox.clear()

    # -- sending ------------------------------------------------------------

    def send(self, msg: dict[str, Any]) -> None:
        """Send one reliable message. Rare by construction -- see protocol.py."""
        if self._sock is None or not self.connected:
            return
        try:
            with self._send_lock:
                protocol.send(self._sock, msg)
        except (OSError, ProtocolError) as exc:
            self.error = str(exc)
            self.connected = False

    def send_state(self, payload: bytes) -> None:
        """Send our dino to the host, which relays it to everyone else.

        Fire and forget. No sequencing beyond the seq inside the packet, no
        acknowledgement, no retry: a lost snapshot is superseded 50 ms later,
        and blocking the Kivy thread to make sure one arrived would cost more
        than the packet was worth.
        """
        if self._udp is None or self._host_endpoint is None:
            self.send(protocol.state_tcp(payload))
            return
        self._maybe_register(timing.now())
        try:
            self._udp.sendto(payload, self._host_endpoint)
        except OSError:
            pass  # the next one is 50 ms away; never block the frame

    def _maybe_register(self, now: float) -> None:
        """Re-announce our UDP endpoint until packets start arriving.

        The host cannot answer a datagram it has never received: it knows our
        TCP socket, which says nothing about which ephemeral port our UDP
        socket landed on. One registration datagram would do it, and one
        datagram can be lost.
        """
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

    def maybe_ping(self) -> None:
        """Send a latency probe at most once per PING_INTERVAL.

        Called from every screen, not just in game: the reply is what keeps
        both ends' keepalive clocks fresh, and a lobby is exactly where you sit
        long enough for a phone to wander off the network. The measurement also
        feeds the world-clock sync (CoopSession.set_flight_time).
        """
        now = timing.now()
        if now - self._last_ping < C.PING_INTERVAL:
            return
        self._last_ping = now
        self.send(protocol.ping(now))

    def begin_run(self) -> None:
        """Reset the gameplay stream for a new run."""
        self._registered = False
        self._last_register = 0.0
        self.state_inbox.clear()
