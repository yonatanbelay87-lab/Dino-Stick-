"""The host's transport. Accepts clients, relays, and arbitrates -- nothing else.

This used to own the simulation and hand everyone else a copy of it. It does
not any more. Every device, host included, runs its own ``CoopSession`` against
the shared seed (game/coop.py), so what is left here is the plumbing:

  * a TCP listener and one reader thread per client, for the reliable channel;
  * a UDP socket and one receive thread, for the 15-byte state stream;
  * a relay, because in a 3- or 4-player room joiner A's snapshots have to
    reach joiner B and the only address B is guaranteed to know is the host's;
  * the lobby roster, and the seed.

Threading model:
  * accept thread, one reader thread per client, one UDP thread -- these only
    ever decode and enqueue;
  * everything that touches the game or a widget happens on the Kivy thread,
    draining those queues.

The host's own player is simply player 0. It has no more authority over its own
dino than any joiner has over theirs, which is the entire point: the code path
that runs a run is the same whether you hosted it or joined it.

What the host DOES still decide is the handful of events that change the shared
world -- which power-up counts, and when. That is arbitration, not simulation:
it names a tick, and every device (including this one) applies the effect there.
"""

from __future__ import annotations

import queue
import random
import selectors
import socket
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from game import constants as C
from game import timing

from . import protocol
from .protocol import FrameReader, ProtocolError
from .statepacket import MSG_STATE, STATE_SIZE

HOST_PLAYER_ID = C.HOST_PLAYER_ID

# How long the UDP thread waits on the selector before looking at the stop
# flag again. The socket itself is non-blocking; this is what stops the thread
# spinning a core while nothing is arriving.
_SELECT_TIMEOUT = 0.5


@dataclass
class ClientConn:
    """One connected client. ``send_lock`` serialises writes to the socket."""

    id: int
    sock: socket.socket
    addr: tuple[str, int]
    name: str = "Player"
    skin: int = 0
    ready: bool = False
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    alive: bool = True
    # When we last heard anything at all from this client. Clients ping once a
    # second in every screen, so silence is the signal that one has gone.
    last_rx: float = field(default_factory=timing.now)


class GameHost:
    """Accepts clients, assigns ids, relays the state stream, names the seed."""

    def __init__(self, name: str = "Player 1", port: int = C.PORT_GAME) -> None:
        # Decoded inbound reliable messages for the Kivy layer to drain on the
        # main thread: (client_id, message) or (client_id, {"type":
        # "_disconnect"}).
        self.inbox: queue.Queue = queue.Queue()
        # Raw state datagrams: (payload, received_at). A deque rather than a
        # Queue because the consumer wants *everything* pending in one go at
        # 20 Hz, and popleft-until-IndexError is cheaper than Queue's
        # per-item locking for a stream this hot.
        self.state_inbox: deque = deque(maxlen=256)

        self.port = port
        self.host_name = name
        self.host_skin = 0
        self.host_ready = True  # the host is always ready; they press Start

        self.seed: int = 0
        self.running = False

        self._server: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._stop = threading.Event()

        # The gameplay stream. Snapshots go out from the Kivy thread via
        # sendto(); everyone else's arrive on a dedicated receive thread.
        self._udp: socket.socket | None = None
        self._udp_thread: threading.Thread | None = None
        # player id -> (ip, port) learned from that client's udp_register.
        self._udp_endpoints: dict[int, tuple[str, int]] = {}

        self._clients: dict[int, ClientConn] = {}
        self._lock = threading.Lock()
        self._next_id = HOST_PLAYER_ID + 1

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("", self.port))
        server.listen(C.MAX_PLAYERS)
        server.settimeout(0.5)
        self._server = server
        self._stop.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop,
                                               daemon=True,
                                               name="dinostick-accept")
        self._accept_thread.start()
        self._start_udp()

    def _start_udp(self) -> None:
        """Bind the gameplay socket. Failure here is not fatal.

        If the UDP port is taken -- a second host on this machine, or another
        program squatting on it -- the game still runs: with no socket, state
        packets fall back to the TCP channel in ``send_state``. That is a worse
        experience (head-of-line blocking on a dropped packet is exactly what
        the UDP path exists to avoid) but it is a game rather than an error.
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", C.PORT_GAME_UDP))
            # Non-blocking, and then parked on a selector below. Belt and
            # braces: nothing on this socket may ever block, and a recvfrom
            # that somehow ran on the wrong thread would return immediately
            # rather than stalling a frame.
            sock.setblocking(False)
        except OSError:
            self._udp = None
            return
        self._udp = sock
        self._udp_thread = threading.Thread(target=self._udp_loop, daemon=True,
                                            name="dinostick-udp-recv")
        self._udp_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._udp is not None:
            try:
                self._udp.close()
            except OSError:
                pass
            self._udp = None
        if self._udp_thread is not None:
            self._udp_thread.join(timeout=1.0)
            self._udp_thread = None
        self._udp_endpoints.clear()
        self.state_inbox.clear()
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.alive = False
            try:
                client.sock.close()
            except OSError:
                pass
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=1.0)
            self._accept_thread = None

    # -- accept / read threads ---------------------------------------------

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                assert self._server is not None
                sock, addr = self._server.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break

            with self._lock:
                # +1 for the host itself, and the count is BEFORE this client
                # is added -- so the room is full once accepting one more would
                # exceed MAX_PLAYERS. Written as MAX_PLAYERS + 1 this let a
                # fifth player into a four-player game.
                full = len(self._clients) + 1 >= C.MAX_PLAYERS
            if full or self.running:
                # No room, or the run already started -- refuse politely.
                try:
                    sock.close()
                except OSError:
                    pass
                continue

            with self._lock:
                cid = self._next_id
                self._next_id += 1
                client = ClientConn(id=cid, sock=sock, addr=addr)
                self._clients[cid] = client

            threading.Thread(target=self._client_loop, args=(client,),
                             daemon=True,
                             name=f"dinostick-client-{cid}").start()

    def _client_loop(self, client: ClientConn) -> None:
        reader = FrameReader()
        client.sock.settimeout(1.0)
        try:
            while not self._stop.is_set() and client.alive:
                try:
                    data = client.sock.recv(4096)
                except (socket.timeout, TimeoutError):
                    # A silent socket is not the same as a closed one. A phone
                    # that leaves Wi-Fi mid-run never closes anything, so
                    # without this the connection stays "alive" for minutes and
                    # that player's dino stands still until it kills the team.
                    if timing.now() - client.last_rx > C.CONNECTION_TIMEOUT:
                        break
                    continue
                except OSError:
                    break
                if not data:
                    break  # peer closed
                client.last_rx = timing.now()
                try:
                    for msg in reader.feed(data):
                        self._on_message(client, msg)
                except ProtocolError:
                    break
        finally:
            self._drop(client)

    # -- UDP gameplay stream -------------------------------------------------

    def _udp_loop(self) -> None:
        """Receive state packets. Own thread, non-blocking socket, no widgets.

        Deliberately does almost nothing: queue the payload for the Kivy thread
        and, in a room of three or more, forward it to the other clients. The
        decode happens where the game is, so a malformed packet cannot take
        down the receive thread and a burst cannot stall it.
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
                # Drain everything readable before going back to the selector:
                # one wakeup can cover several datagrams, and leaving them
                # buffered adds a scheduling round trip to each.
                while True:
                    try:
                        data, addr = sock.recvfrom(2048)
                    except BlockingIOError:
                        break
                    except OSError:
                        return
                    self._on_datagram(data, addr)
        finally:
            selector.close()

    def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) == STATE_SIZE and data[0] == MSG_STATE:
            self.state_inbox.append((data, timing.now()))
            self._relay(data, addr)
            return
        msg = protocol.unpack(data)
        if msg is not None and msg.get(protocol.TYPE) == protocol.MSG_UDP_REGISTER:
            self._on_udp_register(msg, addr)

    def _relay(self, payload: bytes, sender: tuple[str, int]) -> None:
        """Forward one client's state to the others.

        Clients know the host's address and nothing else -- they discovered the
        room, they did not discover each other -- so in a room of three or more
        the host is the only path between two joiners. Sent verbatim: the
        payload already names its own player, and re-packing it here would be
        a second chance to get the quantisation wrong.
        """
        sock = self._udp
        if sock is None:
            return
        for endpoint in list(self._udp_endpoints.values()):
            if endpoint == sender:
                continue
            try:
                sock.sendto(payload, endpoint)
            except OSError:
                pass  # a dropped snapshot is replaced in 50 ms; never block

    def _on_udp_register(self, msg: dict[str, Any],
                         addr: tuple[str, int]) -> None:
        """Learn where to send this player's snapshots.

        The claimed id is checked against the address that client's TCP
        connection came from. Two games running on one subnet will happily
        deliver each other's stray datagrams, and without this a packet from
        the wrong game could redirect a player's whole snapshot stream.
        """
        try:
            pid = int(msg.get("id", -1))
        except (TypeError, ValueError):
            return
        with self._lock:
            client = self._clients.get(pid)
        if client is None or client.addr[0] != addr[0]:
            return
        self._udp_endpoints[pid] = addr
        client.last_rx = timing.now()

    def _on_message(self, client: ClientConn, msg: dict[str, Any]) -> None:
        """Runs on a socket thread: only touch plain data and the queue."""
        kind = msg.get(protocol.TYPE)
        if kind == protocol.MSG_PING:
            # Echo immediately, on this thread: routing it through the main
            # loop would fold the host's frame time into the measurement.
            self.send_to(client, protocol.pong(msg.get("t", 0.0)))
            return
        if kind == protocol.MSG_JOIN:
            client.name = str(msg.get("name", f"Player {client.id + 1}"))[:16]
            client.skin = int(msg.get("skin", client.id)) % len(C.SKIN_COLORS)
            # Answer immediately with this client's own id, rather than waiting
            # for the UI layer to notice the join and push a lobby update. The
            # id is what the client stamps on its udp_register, so leaving it
            # to a pump on another thread makes a networking essential depend
            # on a screen being open.
            self.send_to(client, protocol.lobby(self.roster(), client.id))
        elif kind == protocol.MSG_READY:
            client.ready = bool(msg.get("ready"))
        elif kind == protocol.MSG_SKIN:
            client.skin = int(msg.get("skin", client.skin)) % len(C.SKIN_COLORS)
        self.inbox.put((client.id, msg))

    def _drop(self, client: ClientConn) -> None:
        client.alive = False
        with self._lock:
            self._clients.pop(client.id, None)
        self._udp_endpoints.pop(client.id, None)
        try:
            client.sock.close()
        except OSError:
            pass
        self.inbox.put((client.id, {protocol.TYPE: "_disconnect"}))

    # -- sending ------------------------------------------------------------

    def broadcast(self, msg: dict[str, Any]) -> None:
        with self._lock:
            clients = list(self._clients.values())
        for client in clients:
            self.send_to(client, msg)

    def send_to(self, client: ClientConn, msg: dict[str, Any]) -> None:
        if not client.alive:
            return
        try:
            with client.send_lock:
                protocol.send(client.sock, msg)
        except (OSError, ProtocolError):
            self._drop(client)

    def send_state(self, payload: bytes) -> None:
        """Fan the host's own dino out to every registered client.

        Fire and forget in the strictest sense: a failed send is discarded
        without a retry and without a log line. The next one is 50 ms away and
        supersedes it, and the one thing this call must never do is block the
        Kivy thread it is running on.
        """
        sock = self._udp
        if sock is None:
            # No UDP socket at all -- see MSG_STATE_TCP. Badly, but visibly.
            self.broadcast(protocol.state_tcp(payload))
            return
        for endpoint in list(self._udp_endpoints.values()):
            try:
                sock.sendto(payload, endpoint)
            except OSError:
                pass

    # -- roster -------------------------------------------------------------

    def roster(self) -> list[dict[str, Any]]:
        """Lobby view of everyone, host first, ordered by player id."""
        with self._lock:
            clients = sorted(self._clients.values(), key=lambda c: c.id)
        entries = [{
            "id": HOST_PLAYER_ID,
            "name": self.host_name,
            "skin": self.host_skin,
            "ready": self.host_ready,
            "host": True,
        }]
        entries += [{"id": c.id, "name": c.name, "skin": c.skin,
                     "ready": c.ready, "host": False} for c in clients]
        return entries

    def player_count(self) -> int:
        with self._lock:
            return len(self._clients) + 1

    def everyone_ready(self) -> bool:
        with self._lock:
            clients = list(self._clients.values())
        return all(c.ready for c in clients)

    def push_lobby(self) -> None:
        """Send each client the roster plus which entry is them."""
        players = self.roster()
        with self._lock:
            clients = list(self._clients.values())
        for client in clients:
            self.send_to(client, protocol.lobby(players, client.id))

    # -- starting a run ------------------------------------------------------

    def start_game(self, seed: int | None = None) -> list[dict[str, Any]]:
        """Name the world and start the countdown. Returns the roster.

        Two messages, in this order, both reliable:

          SEED   the whole world, as one integer. Every obstacle in the run is
                 derived from it identically on every device, so this is the
                 only thing anyone is ever told about the course.
          START  go, in ``countdown`` seconds. The delay is what makes the
                 start synchronised rather than staggered: without it the
                 joiner reaches tick 0 half a round trip late and stays that
                 far behind in a world where the tick number is the layout.

        The host does not simulate on anyone's behalf here. It builds the same
        session everyone else does, from the same seed, and plays.
        """
        self.seed = random.randrange(1 << 31) if seed is None else seed
        entries = self.roster()
        self.running = True
        self.state_inbox.clear()
        self.broadcast(protocol.seed_msg(self.seed, entries))
        self.broadcast(protocol.start(self.seed, entries, C.START_COUNTDOWN))
        return entries

    def end_game(self) -> None:
        self.running = False
