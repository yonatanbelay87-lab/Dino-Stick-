"""Dino Stick -- app entry point.

Builds the ScreenManager, and owns the network session plus the single pump
that drains socket queues on the main thread.

Run with:

    python main.py

from inside the ``dinostick/`` directory (the package imports are flat:
``game``, ``net``, ``screens``, ``ui``).
"""

from __future__ import annotations

import os
import queue
import sys
import time
from collections import deque

# Allow `python main.py` from anywhere: put this directory on sys.path so the
# flat package imports (game/, net/, screens/, ui/) resolve.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kivy.app import App  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.uix.screenmanager import NoTransition, ScreenManager  # noqa: E402

from game import constants as C  # noqa: E402
from game.entities import snapshot_to_state  # noqa: E402
from net import protocol  # noqa: E402
from net.client import GameClient  # noqa: E402
from net.discovery import HostAnnouncer, get_local_ip  # noqa: E402
from net.host import GameHost  # noqa: E402
from screens import GAME, GAMEOVER, LOBBY, MENU  # noqa: E402
from screens.game import GameScreen  # noqa: E402
from screens.gameover import GameOverScreen  # noqa: E402
from screens.lobby import LobbyScreen  # noqa: E402
from screens.menu import MenuScreen  # noqa: E402
from ui import settings  # noqa: E402

MODE_LOCAL = "local"
MODE_HOST = "host"
MODE_CLIENT = "client"

# Android delivers Back as keycode 27, the same as ESC.
KEY_BACK = 27


def _keep_screen_on() -> None:
    """Stop Android blanking the screen mid-run.

    A runner is played without touching the screen for long stretches, so the
    display would otherwise dim and sleep during a good run. Best-effort: any
    failure here must not stop the game from starting.
    """
    if "ANDROID_ARGUMENT" not in os.environ:
        return
    try:
        from jnius import autoclass  # noqa: PLC0415 -- Android only

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        LayoutParams = autoclass("android.view.WindowManager$LayoutParams")
        activity = PythonActivity.mActivity
        activity.getWindow().addFlags(LayoutParams.FLAG_KEEP_SCREEN_ON)
    except Exception:
        pass


class DinoStickApp(App):
    title = C.APP_NAME

    def build(self) -> ScreenManager:
        Window.clearcolor = C.COLOR_BG
        _keep_screen_on()

        # Without this the Android soft keyboard covers whatever you are
        # typing into: the window does not move, it just draws the keyboard on
        # top. In landscape the keyboard is most of the screen, so the name and
        # address fields were both invisible while being edited.
        Window.softinput_mode = "pan"

        self.mode: str = MODE_LOCAL
        self.host: GameHost | None = None
        self.client: GameClient | None = None
        self.announcer: HostAnnouncer | None = None

        stored = settings.load(self.user_data_dir)
        self.player_name: str = stored["player_name"]
        self.skin: int = int(stored["skin"]) % len(C.SKIN_NAMES)
        # id -> (name, skin), used to dress up the snapshots clients render.
        self.roster: dict[int, tuple[str, int]] = {}
        self.lobby_players: list[dict] = []

        # Timestamped snapshots awaiting interpolation (client mode only).
        self.snapshots: deque = deque(maxlen=C.SNAPSHOT_BUFFER)
        self.last_gameover: dict | None = None

        sm = ScreenManager(transition=NoTransition())
        sm.add_widget(MenuScreen(name=MENU))
        sm.add_widget(LobbyScreen(name=LOBBY))
        sm.add_widget(GameScreen(name=GAME))
        sm.add_widget(GameOverScreen(name=GAMEOVER))
        sm.current = MENU
        self.sm = sm

        # One pump for all network traffic. Every socket thread hands us plain
        # dicts through a Queue; this is the only place they are consumed, and
        # it runs on the Kivy main thread, so screens are safe to touch here.
        Clock.schedule_interval(self._pump, 0)

        Window.bind(on_keyboard=self._on_back)
        return sm

    # -- Android Back ------------------------------------------------------

    def _on_back(self, _window, key, *_args) -> bool:
        """Back/ESC steps back through the app instead of killing it.

        On Android the default behaviour is to close the app outright, which
        would silently drop a hosted game and everyone connected to it.
        """
        if key != KEY_BACK:
            return False

        current = self.sm.current
        if current == GAME:
            self.sm.get_screen(GAME)._quit()
        elif current in (LOBBY, GAMEOVER):
            self.leave_network()
            self.sm.current = MENU
        else:
            return False  # on the menu, let Back exit the app
        return True

    # -- session control ----------------------------------------------------

    def start_hosting(self) -> bool:
        self.leave_network()
        host = GameHost(name=self.player_name)
        try:
            host.start()
        except OSError as exc:
            self.sm.get_screen(MENU).show_error(f"Could not host: {exc}")
            return False
        host.host_skin = self.skin
        self.host = host
        self.mode = MODE_HOST

        self.announcer = HostAnnouncer(f"{self.player_name}'s game")
        self.announcer.start()
        return True

    def join_game(self, ip: str, port: int = C.PORT_GAME) -> bool:
        self.leave_network()
        client = GameClient(name=self.player_name, skin=self.skin)
        try:
            client.connect(ip, port)
        except OSError as exc:
            self.sm.get_screen(MENU).show_error(f"Could not join {ip}: {exc}")
            return False
        self.client = client
        self.mode = MODE_CLIENT
        return True

    def start_local(self) -> None:
        self.leave_network()
        self.mode = MODE_LOCAL

    def leave_network(self) -> None:
        if self.announcer is not None:
            self.announcer.stop()
            self.announcer = None
        if self.host is not None:
            self.host.stop()
            self.host = None
        if self.client is not None:
            self.client.disconnect()
            self.client = None
        self.snapshots.clear()
        self.roster.clear()
        self.lobby_players.clear()
        self.mode = MODE_LOCAL

    def local_ip(self) -> str:
        return get_local_ip()

    # -- the pump -----------------------------------------------------------

    def _pump(self, _dt: float) -> None:
        if self.mode == MODE_HOST and self.host is not None:
            self._pump_host()
        elif self.mode == MODE_CLIENT and self.client is not None:
            self._pump_client()

    def _pump_host(self) -> None:
        host = self.host
        if host is None:
            return
        changed = False
        while self.host is host:
            try:
                _cid, msg = host.inbox.get_nowait()
            except queue.Empty:
                break
            kind = msg.get(protocol.TYPE)
            if kind in (protocol.MSG_JOIN, protocol.MSG_READY,
                        protocol.MSG_SKIN, "_disconnect"):
                changed = True

        if changed and self.host is host:
            host.push_lobby()
            if self.announcer is not None:
                self.announcer.player_count = host.player_count()
            if self.sm.current == LOBBY:
                self.sm.get_screen(LOBBY).refresh()

    def _pump_client(self) -> None:
        client = self.client
        if client is None:
            return
        # Pinged from here rather than from the game screen, so the keepalive
        # runs in the lobby and on the game-over card too -- those are exactly
        # where you sit still long enough to lose Wi-Fi without noticing.
        client.maybe_ping()
        # `self.client is client` because handling a message can tear the
        # session down: a "_disconnected" sets self.client to None, and going
        # round again on the old reference crashed the app on every dropped
        # connection -- the one moment it most needs to survive.
        while self.client is client:
            try:
                msg = client.inbox.get_nowait()
            except queue.Empty:
                break
            self._on_client_message(msg)

    def _on_client_message(self, msg: dict) -> None:
        kind = msg.get(protocol.TYPE)

        if kind == protocol.MSG_LOBBY:
            self.lobby_players = msg.get("players", [])
            self.roster = {int(p["id"]): (p["name"], int(p["skin"]))
                           for p in self.lobby_players}
            self.sm.get_screen(LOBBY).refresh()

        elif kind == protocol.MSG_START:
            players = msg.get("players", [])
            self.roster = {int(p["id"]): (p["name"], int(p["skin"]))
                           for p in players}
            self.snapshots.clear()
            self.sm.current = GAME

        elif kind == protocol.MSG_STATE:
            state = snapshot_to_state(msg, self.roster)
            self.snapshots.append((time.monotonic(), state))

        elif kind == protocol.MSG_GAMEOVER:
            cause = msg.get("cause") or {}
            # Run length is not on the wire; the last snapshot we rendered
            # carries the tick it ended on, which is the same number.
            ticks = self.snapshots[-1][1].tick if self.snapshots else None
            self.last_gameover = {
                "score": int(msg.get("score", 0)),
                "distance": float(msg.get("distance", 0.0)),
                "cause_player_id": cause.get("player_id"),
                "cause_obstacle": cause.get("obstacle"),
                "players": len(self.roster) or 1,
                "seconds": None if ticks is None else ticks * C.TICK_DT,
            }
            self.sm.get_screen(GAMEOVER).show_result(**self.last_gameover)
            self.sm.current = GAMEOVER

        elif kind == protocol.MSG_REMATCH:
            self.snapshots.clear()

        elif kind == "_disconnected":
            # The client knows *why* when it timed out rather than being
            # closed; "lost connection" alone leaves people wondering whether
            # they did something wrong.
            reason = (self.client.error if self.client is not None
                      else None) or "Lost connection to the host."
            self.leave_network()
            menu = self.sm.get_screen(MENU)
            menu.show_error(reason)
            self.sm.current = MENU

    def on_stop(self) -> None:
        self.leave_network()


if __name__ == "__main__":
    DinoStickApp().run()
