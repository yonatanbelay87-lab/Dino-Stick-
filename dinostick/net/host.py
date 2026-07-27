"""TCP game server. Owns the simulation and broadcasts state to all clients.

Threading model:
  * one accept thread, one reader thread per client -- these only ever decode
    frames and push dicts into ``inbox``;
  * the *caller's* clock drives ``tick()``, which is where the simulation
    advances and state goes out.

That last point is deliberate. The host owns the Simulation object, but it does
not own a clock: the Kivy layer calls ``tick(dt)`` from its Clock callback, so
the simulation only ever runs on the main thread. Nothing needs a lock around
sim state, and no socket thread can touch a widget.

The host's own player is simply player 0 -- there is no special-casing, which
keeps the sim identical whether you host or join.
"""

from __future__ import annotations

import queue
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from game import constants as C
from game.entities import Player, state_to_snapshot
from game.simulation import Simulation

from . import protocol
from .protocol import FrameReader, ProtocolError

HOST_PLAYER_ID = 0


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
    last_rx: float = field(default_factory=time.monotonic)


class GameHost:
    """Accepts clients, assigns ids, drives the authoritative simulation."""

    def __init__(self, name: str = "Player 1", port: int = C.PORT_GAME) -> None:
        # Decoded inbound messages for the Kivy layer to drain on the main
        # thread: (client_id, message) or (client_id, {"type": "_disconnect"}).
        self.inbox: queue.Queue = queue.Queue()

        self.port = port
        self.host_name = name
        self.host_skin = 0
        self.host_ready = True  # the host is always ready; they press Start

        self.sim: Simulation | None = None
        self.seed: int = 0
        self.running = False

        self._server: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._stop = threading.Event()

        self._clients: dict[int, ClientConn] = {}
        self._lock = threading.Lock()
        self._next_id = HOST_PLAYER_ID + 1

        # Latest input per player id, applied at the top of each tick.
        self._inputs: dict[int, tuple[bool, bool]] = {}

        self._accumulator = 0.0
        self._broadcast_timer = 0.0
        # Sim events pile up here between broadcasts. Ticks run at 60 Hz but
        # state goes out at 30, so reading state.events at broadcast time would
        # drop half the jumps and lands on the clients.
        self._event_buffer: list[dict] = []

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

    def stop(self) -> None:
        self._stop.set()
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
                    if time.monotonic() - client.last_rx > C.CONNECTION_TIMEOUT:
                        break
                    continue
                except OSError:
                    break
                if not data:
                    break  # peer closed
                client.last_rx = time.monotonic()
                try:
                    for msg in reader.feed(data):
                        self._on_message(client, msg)
                except ProtocolError:
                    break
        finally:
            self._drop(client)

    def _on_message(self, client: ClientConn, msg: dict[str, Any]) -> None:
        """Runs on a socket thread: only touch plain data and the queue."""
        kind = msg.get(protocol.TYPE)
        if kind == protocol.MSG_PING:
            # Echo immediately, on this thread: routing it through the main
            # loop would fold the host's frame time into the measurement.
            self.send_to(client, protocol.pong(msg.get("t", 0.0)))
            return
        if kind == protocol.MSG_INPUT:
            # Latest-wins. A tuple assignment into a dict is atomic under the
            # GIL, so the main thread can read this without a lock.
            self._inputs[client.id] = (bool(msg.get("jump")),
                                       bool(msg.get("duck")))
            return
        if kind == protocol.MSG_JOIN:
            client.name = str(msg.get("name", f"Player {client.id + 1}"))[:16]
            client.skin = int(msg.get("skin", client.id)) % len(C.SKIN_COLORS)
        elif kind == protocol.MSG_READY:
            client.ready = bool(msg.get("ready"))
        elif kind == protocol.MSG_SKIN:
            client.skin = int(msg.get("skin", client.skin)) % len(C.SKIN_COLORS)
        self.inbox.put((client.id, msg))

    def _drop(self, client: ClientConn) -> None:
        client.alive = False
        with self._lock:
            self._clients.pop(client.id, None)
        try:
            client.sock.close()
        except OSError:
            pass
        self._inputs.pop(client.id, None)
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

    # -- game ---------------------------------------------------------------

    def start_game(self, seed: int | None = None) -> None:
        """Build the simulation and tell everyone to start on the same seed."""
        self.seed = random.randrange(1 << 31) if seed is None else seed
        entries = self.roster()

        players = [
            Player(
                id=e["id"],
                name=e["name"],
                skin=e["skin"],
                x=C.PLAYER_START_X + index * C.PLAYER_SPACING_X,
            )
            for index, e in enumerate(entries)
        ]
        self.sim = Simulation(self.seed, players)
        self._inputs.clear()
        self._accumulator = 0.0
        self._broadcast_timer = 0.0
        self.running = True
        self.broadcast(protocol.start(self.seed, entries))

    def set_local_input(self, jump: bool, duck: bool) -> None:
        """The host's own keyboard/touch, applied to player 0."""
        self._inputs[HOST_PLAYER_ID] = (jump, duck)

    def tick(self, frame_dt: float) -> None:
        """Advance the authoritative sim and broadcast, on the caller's clock.

        Same fixed-timestep accumulator as single player: real elapsed time is
        banked and spent in whole TICK_DT slices, so physics never varies with
        the host's framerate.
        """
        if not self.running or self.sim is None:
            return

        for pid, (jump, duck) in list(self._inputs.items()):
            self.sim.set_input(pid, jump, duck)

        self._accumulator += min(frame_dt, C.MAX_FRAME_DT)
        ticks = 0
        while (self._accumulator >= C.TICK_DT
               and ticks < C.MAX_TICKS_PER_FRAME):
            self.sim.step(C.TICK_DT)
            self._event_buffer.extend(self.sim.state.events)
            self._accumulator -= C.TICK_DT
            ticks += 1
            if not self.sim.state.running:
                break
        if ticks == C.MAX_TICKS_PER_FRAME:
            self._accumulator = 0.0

        state = self.sim.state
        if not state.running:
            self.running = False
            self.broadcast(protocol.gameover(
                state.score, state.distance,
                state.cause_player_id, state.cause_obstacle))
            return

        # State goes out at STATE_BROADCAST_HZ, not every physics tick -- 60 Hz
        # of full snapshots is bandwidth we do not need on a LAN.
        self._broadcast_timer += frame_dt
        interval = 1.0 / C.STATE_BROADCAST_HZ
        if self._broadcast_timer >= interval:
            self._broadcast_timer = 0.0
            msg = state_to_snapshot(state)
            msg[protocol.TYPE] = protocol.MSG_STATE
            msg["events"] = self._event_buffer
            self._event_buffer = []
            self.broadcast(msg)
