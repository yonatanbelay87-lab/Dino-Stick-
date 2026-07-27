"""UDP broadcast discovery: hosts announce themselves, clients listen.

Discovery is a convenience, never a requirement -- broadcast is blocked on
plenty of networks and needs a multicast lock on Android. The join UI always
offers manual IP entry as well, so the game stays playable when this finds
nothing.

Announcements are plain JSON datagrams with no length prefix: UDP preserves
message boundaries, so the framing that TCP needs would be pointless here.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import threading
import time
from typing import Any

from game import constants as C

from . import protocol

BROADCAST_ADDR = "255.255.255.255"


# ---------------------------------------------------------------------------
# Android multicast lock
# ---------------------------------------------------------------------------
#
# Android drops broadcast and multicast packets before they ever reach a socket
# unless the app holds a WifiManager.MulticastLock. Without this, discovery is
# silently dead on a phone -- the socket binds fine and simply never receives.
#
# Note the Android check is `ANDROID_ARGUMENT`, an environment variable set by
# python-for-android, rather than kivy.utils.platform: net/ must not import
# Kivy. Everything here degrades to a no-op off-device, and the manual IP entry
# path in the join dialog works regardless of whether any of it succeeds.


def is_android() -> bool:
    return "ANDROID_ARGUMENT" in os.environ


class MulticastLock:
    """Holds a WifiManager.MulticastLock while listening, if we are on Android."""

    def __init__(self, tag: str = "dinostick") -> None:
        self.tag = tag
        self._lock = None

    def acquire(self) -> bool:
        if not is_android() or self._lock is not None:
            return False
        try:
            from jnius import autoclass  # noqa: PLC0415 -- Android only

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Context = autoclass("android.content.Context")
            activity = PythonActivity.mActivity
            wifi = activity.getSystemService(Context.WIFI_SERVICE)
            lock = wifi.createMulticastLock(self.tag)
            lock.setReferenceCounted(True)
            lock.acquire()
            self._lock = lock
            return True
        except Exception:
            # Vendor quirks, missing permission, pyjnius unavailable -- none of
            # these should stop the game starting.
            self._lock = None
            return False

    def release(self) -> None:
        if self._lock is None:
            return
        try:
            self._lock.release()
        except Exception:
            pass
        self._lock = None


# Probed in order to find our own LAN address. The first is the usual trick --
# ask the OS which interface it would use to reach the internet -- and the rest
# exist for the case this game is most often played in: a phone acting as a
# hotspot with mobile data off. That phone has NO default route, so probing
# 8.8.8.8 fails outright and the lobby used to advertise 127.0.0.1, which
# nobody can join. Probing an address inside the hotspot's own subnet needs
# only the local route, which does exist.
#
# No packet is ever sent: connect() on a UDP socket just fixes the peer, which
# is enough to make getsockname() report the interface the kernel picked.
_IP_PROBES: tuple[tuple[str, int], ...] = (
    ("8.8.8.8", 80),  # default route, if there is one
    ("192.168.43.1", 80),  # Android hotspot gateway
    ("172.20.10.1", 80),  # iOS hotspot gateway
    ("192.168.1.1", 80),  # common home router
    ("10.0.0.1", 80),
)


def get_local_ip() -> str:
    """Best guess at this machine's LAN address, for display and manual join."""
    for target in _IP_PROBES:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(target)
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            continue
        finally:
            sock.close()
    return "127.0.0.1"


def broadcast_addresses() -> list[str]:
    """Where to aim announcements.

    255.255.255.255 alone is not enough on a phone. It is routed via the
    DEFAULT route, so a handset with mobile data up sends every announcement
    out the cellular interface, where no one is listening -- while the game's
    own TCP traffic works fine, because a LAN address matches a more specific
    route. Adding the /24-directed broadcast for our own subnet (x.y.z.255)
    forces the packet onto the Wi-Fi interface instead.

    Assuming /24 is a heuristic, but it holds for essentially every home
    router and every phone hotspot, and the global broadcast is still sent
    alongside it, so a /16 network loses nothing.
    """
    targets = [BROADCAST_ADDR]
    ip = get_local_ip()
    parts = ip.split(".")
    if len(parts) == 4 and not ip.startswith("127."):
        directed = ".".join(parts[:3] + ["255"])
        if directed != BROADCAST_ADDR:
            targets.append(directed)
    return targets


class HostAnnouncer:
    """Periodically broadcasts this host's presence on PORT_DISCOVERY."""

    def __init__(self, name: str, game_port: int = C.PORT_GAME) -> None:
        self.name = name
        self.game_port = game_port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._targets: list[str] = [BROADCAST_ADDR]
        # Read by the announce thread, written by the main thread.
        self.player_count = 1

    def start(self) -> None:
        if self._thread is not None:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Bind to the LAN interface so announcements leave by the same route
        # the game itself uses. Unbound, a phone with mobile data would send
        # them out the cellular interface. Best effort: if the address has gone
        # (Wi-Fi dropped between here and now) fall back to unbound rather than
        # refusing to host.
        try:
            self._sock.bind((get_local_ip(), 0))
        except OSError:
            pass
        self._targets = broadcast_addresses()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="dinostick-announce")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            msg = protocol.announce(self.name, self.game_port,
                                    self.player_count, C.MAX_PLAYERS)
            payload = json.dumps(msg).encode("utf-8")
            for target in self._targets:
                try:
                    assert self._sock is not None
                    self._sock.sendto(payload, (target, C.PORT_DISCOVERY))
                except OSError:
                    pass  # interface down / no route -- manual IP still works
            self._stop.wait(C.DISCOVERY_INTERVAL)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None


class ClientDiscovery:
    """Listens for announcements and reports the hosts it has seen."""

    def __init__(self) -> None:
        self.found: queue.Queue = queue.Queue()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # ip -> host record, refreshed as announcements arrive.
        self._seen: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._multicast = MulticastLock()
        # True when we are on Android and actually got the lock; surfaced so
        # the join UI can hint at manual IP entry when discovery is hobbled.
        self.multicast_locked = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self.multicast_locked = self._multicast.acquire()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.5)
        sock.bind(("", C.PORT_DISCOVERY))
        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="dinostick-discover")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                assert self._sock is not None
                data, addr = self._sock.recvfrom(2048)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if msg.get(protocol.TYPE) != protocol.MSG_ANNOUNCE:
                continue
            record = {
                "ip": addr[0],
                "name": str(msg.get("name", "Dino Stick host")),
                "port": int(msg.get("game_port", C.PORT_GAME)),
                "players": int(msg.get("players", 1)),
                "max": int(msg.get("max", C.MAX_PLAYERS)),
                "seen_at": time.monotonic(),
            }
            with self._lock:
                self._seen[record["ip"]] = record
            self.found.put(record)

    def hosts(self) -> list[dict[str, Any]]:
        """Hosts heard from recently, newest announcement first."""
        now = time.monotonic()
        with self._lock:
            fresh = [h for h in self._seen.values()
                     if now - h["seen_at"] <= C.DISCOVERY_TIMEOUT]
            self._seen = {h["ip"]: h for h in fresh}
        return sorted(fresh, key=lambda h: h["name"])

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._multicast.release()
        self.multicast_locked = False
