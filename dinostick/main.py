"""Dino Stick -- app entry point.

Builds the ScreenManager, and owns the network session plus the single pump
that drains socket queues on the main thread.

Run with

    python main.py

from inside the ``dinostick/`` directory (the package imports are flat:
``game``, ``net``, ``screens``, ``ui``).
"""

from __future__ import annotations

import os
import queue
import sys

# Allow `python main.py` from anywhere: put this directory on sys.path so the
# flat package imports (game/, net/, screens/, ui/) resolve.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kivy.app import App  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.logger import Logger  # noqa: E402
from kivy.uix.modalview import ModalView  # noqa: E402
from kivy.uix.screenmanager import (FadeTransition, NoTransition,  # noqa: E402
                                    ScreenManager)

from game import constants as C  # noqa: E402
from game.coop import CoopSession  # noqa: E402
from game.entities import Player  # noqa: E402
from net import protocol  # noqa: E402
from net.client import GameClient  # noqa: E402
from net.discovery import HostAnnouncer, get_local_ip  # noqa: E402
from net.host import GameHost  # noqa: E402
from net.peers import wrap_if_simulating  # noqa: E402
from screens import GAME, GAMEOVER, LOADING, LOBBY, MENU  # noqa: E402
from screens.game import GameScreen  # noqa: E402
from screens.gameover import GameOverScreen  # noqa: E402
from screens.loading import LoadingScreen  # noqa: E402
from screens.lobby import LobbyScreen  # noqa: E402
from screens.menu import MenuScreen  # noqa: E402
from ui import fonts  # noqa: E402
from ui import settings  # noqa: E402
from ui import theme  # noqa: E402
from ui.insets import insets  # noqa: E402

MODE_LOCAL = "local"
MODE_HOST = "host"
MODE_CLIENT = "client"

# Reliable messages that change the shared world. Everyone has to end up
# applying the same set of these, so on the host they are both handled locally
# and relayed on -- a joiner's death has to reach the OTHER joiners, and the
# host is the only address they all have.
_SHARED_EVENTS = frozenset({
    protocol.MSG_POWERUP_CLAIM,
    protocol.MSG_POWERUP,
    protocol.MSG_DEATH,
    protocol.MSG_REVIVE,
    protocol.MSG_SHIELD,
    protocol.MSG_SCORE_SYNC,
})

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
        # The sunset scene paints over this; it is only ever seen for the one
        # frame before the first screen lays out.
        Window.clearcolor = theme.SKY_MID
        _keep_screen_on()

        # Fonts BEFORE any widget exists, or the first labels are built against
        # the default font and keep it. Purely local files -- see ui/fonts.py;
        # a missing .ttf logs a warning and falls back to Kivy's own Roboto.
        fonts.register()
        Logger.info(fonts.report())

        # Start measuring the notch and the gesture bar before any screen is
        # built, so the first frame is already laid out inside the safe area
        # rather than snapping into place once the first poll lands.
        insets.start()

        # Without this the Android soft keyboard covers whatever you are
        # typing into: the window does not move, it just draws the keyboard on
        # top. In landscape the keyboard is most of the screen, so the name and
        # address fields were both invisible while being edited.
        Window.softinput_mode = "pan"

        self.mode: str = MODE_LOCAL
        self.host: GameHost | None = None
        self.client: GameClient | None = None
        self.announcer: HostAnnouncer | None = None
        # This device's half of a networked run. Host and joiner both get one,
        # and they are the same class -- see game/coop.py.
        self.session: CoopSession | None = None

        stored = settings.load(self.user_data_dir)
        self.player_name: str = stored["player_name"]
        self.skin: int = int(stored["skin"]) % len(C.SKIN_NAMES)
        # id -> (name, skin), for the nameplates over each dino.
        self.roster: dict[int, tuple[str, int]] = {}
        self.lobby_players: list[dict] = []
        self.last_gameover: dict | None = None

        sm = ScreenManager(transition=self._transition())
        sm.add_widget(LoadingScreen(name=LOADING))
        sm.add_widget(MenuScreen(name=MENU))
        sm.add_widget(LobbyScreen(name=LOBBY))
        sm.add_widget(GameScreen(name=GAME))
        sm.add_widget(GameOverScreen(name=GAMEOVER))
        # The boot screen is the first thing shown and is never returned to.
        sm.current = LOADING
        self.sm = sm

        # One pump for all network traffic. Every socket thread hands us plain
        # dicts through a Queue; this is the only place they are consumed, and
        # it runs on the Kivy main thread, so screens are safe to touch here.
        Clock.schedule_interval(self._pump, 0)

        Window.bind(on_keyboard=self._on_back)
        return sm

    @staticmethod
    def _transition():
        """Cross-fade between screens, unless the player wants less motion.

        A fade rather than a slide: the shell screens all share the same sunset
        background, so sliding would drag the identical scene sideways behind
        the content and read as a glitch. Kept to ~0.2s -- long enough to feel
        deliberate, short enough that it never gets in the way of a rematch.
        """
        if theme.REDUCE_MOTION:
            return NoTransition()
        return FadeTransition(duration=0.22)

    # -- Android Back ------------------------------------------------------

    def _on_back(self, _window, key, *_args) -> bool:
        """Back/ESC steps back through the app instead of killing it.

        On Android the default behaviour is to close the app outright, which
        would silently drop a hosted game and everyone connected to it.
        """
        if key != KEY_BACK:
            return False

        # A dialog is its own Back level, and Kivy's ModalView already listens
        # for this key to close itself. Without this guard both handlers fire
        # from one press: the dialog closes AND the screen behind it navigates,
        # so a single Back on the join dialog dropped you two levels.
        if any(isinstance(child, ModalView) for child in Window.children):
            return False

        current = self.sm.current
        if current == LOADING:
            return True  # swallow it: there is nothing to go back to yet
        if current == GAME:
            # Asks first -- see GameScreen.request_exit. Back is an edge swipe
            # in landscape and gets hit by accident.
            self.sm.get_screen(GAME).request_exit()
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
        self.session = None
        self.roster.clear()
        self.lobby_players.clear()
        self.mode = MODE_LOCAL

    def local_ip(self) -> str:
        return get_local_ip()

    # -- the co-op session ---------------------------------------------------

    def host_start_game(self) -> None:
        """Host: name a seed, tell everyone, and build our own session.

        The host's session is built from exactly the same call the joiners'
        are, with the same seed and the same countdown. There is no
        "authoritative" variant of it -- if there were, the whole design would
        have a fast path for whoever pressed Start and a slow one for everybody
        else, which is the bug this replaced.
        """
        if self.host is None:
            return
        entries = self.host.start_game()
        self.begin_session(self.host.seed, entries, C.HOST_PLAYER_ID,
                           C.START_COUNTDOWN)

    def begin_session(self, seed: int, entries: list[dict], local_id: int,
                      countdown: float) -> None:
        """Build this device's run from the seed everyone was given."""
        self.roster = {int(e["id"]): (e["name"], int(e["skin"]))
                       for e in entries}
        players = [
            Player(
                id=int(e["id"]),
                name=e["name"],
                skin=int(e["skin"]),
                x=C.PLAYER_START_X + index * C.PLAYER_SPACING_X,
            )
            for index, e in enumerate(entries)
        ]

        if self.mode == MODE_HOST and self.host is not None:
            send_state = self.host.send_state
            send_reliable = self.host.broadcast
        elif self.client is not None:
            send_state = self.client.send_state
            send_reliable = self.client.send
            self.client.begin_run()
        else:
            return

        # NET_SIM_* only: normally this hands the sender straight back. When
        # the knobs are on it wraps the OUTBOUND stream in drops and jitter,
        # which is the honest place to inject them -- see net/peers.py.
        self.session = CoopSession(
            seed=seed,
            players=players,
            local_id=local_id,
            send_state=wrap_if_simulating(send_state),
            send_reliable=send_reliable,
            is_host=self.mode == MODE_HOST,
            countdown=countdown,
        )

    def state_inbox(self):
        """The deque the receive thread is filling, whichever role we are."""
        if self.mode == MODE_HOST and self.host is not None:
            return self.host.state_inbox
        if self.client is not None:
            return self.client.state_inbox
        return None

    def on_run_ended(self, state) -> None:
        """The game screen has finished a networked run.

        The host publishes the final numbers so every device shows the same
        card. Both computed the same score independently -- this settles the
        last tick or two of difference rather than leaving two players staring
        at scores that differ by 3.
        """
        if self.mode == MODE_HOST and self.host is not None:
            self.host.broadcast(protocol.gameover(
                state.score, state.distance,
                state.cause_player_id, state.cause_obstacle))
            self.host.end_game()

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
            elif kind in _SHARED_EVENTS:
                self._on_shared_event(msg, relay=True)

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

        # The gameplay stream is NOT drained here. It arrives on its own socket
        # and its own thread, and it is emptied by the game screen's 20 Hz net
        # tick -- which is the point of having one. Draining it on the frame
        # pump would put it right back on the render loop's critical path.

    def _on_shared_event(self, msg: dict, relay: bool) -> None:
        """Apply one shared-world event here, and pass it on if we are hosting.

        A claim is the exception: it is a question addressed to the host, and
        the host answers it with a POWERUP naming the tick. Relaying the
        question would have every device try to answer it.
        """
        if self.session is not None:
            self.session.on_reliable(msg)
        if (relay and self.host is not None
                and msg.get(protocol.TYPE) != protocol.MSG_POWERUP_CLAIM):
            self.host.broadcast(msg)

    def _on_client_message(self, msg: dict) -> None:
        kind = msg.get(protocol.TYPE)

        if kind == protocol.MSG_LOBBY:
            self.lobby_players = msg.get("players", [])
            self.roster = {int(p["id"]): (p["name"], int(p["skin"]))
                           for p in self.lobby_players}
            self.sm.get_screen(LOBBY).refresh()

        elif kind == protocol.MSG_SEED:
            # The whole world, as one integer. START follows immediately and
            # carries it again, so nothing is lost by treating this as
            # informational -- it is here because a seed and a starting gun are
            # different statements, and a future pre-run screen wants the first
            # without the second.
            pass

        elif kind == protocol.MSG_START:
            players = msg.get("players", [])
            you = (self.client.player_id if self.client is not None else None)
            if you is None:
                return  # no id yet: we cannot know which dino is ours
            # The countdown is measured from when the host SENT this, so the
            # flight time has already been spent. Subtracting it is what makes
            # both devices reach tick 0 together rather than a trip apart.
            countdown = float(msg.get("countdown", 0.0))
            if self.client is not None and self.client.rtt is not None:
                countdown -= self.client.rtt * 0.5
            self.begin_session(int(msg.get("seed", 0)), players, you,
                               max(0.0, countdown))
            self.sm.current = GAME

        elif kind in _SHARED_EVENTS:
            self._on_shared_event(msg, relay=False)

        elif kind == protocol.MSG_GAMEOVER:
            cause = msg.get("cause") or {}
            ticks = (self.session.state.tick if self.session is not None
                     else None)
            self.last_gameover = {
                "score": int(msg.get("score", 0)),
                "distance": float(msg.get("distance", 0.0)),
                "cause_player_id": cause.get("player_id"),
                "cause_obstacle": cause.get("obstacle"),
                "players": len(self.roster) or 1,
                "seconds": None if ticks is None else ticks * C.TICK_DT,
            }
            # Usually a correction rather than news: this device ended its own
            # run the moment it heard about the death, and is already looking
            # at this card. The host's numbers settle the last tick or two of
            # difference so both players read the same score.
            self.sm.get_screen(GAMEOVER).show_result(**self.last_gameover)
            self.sm.current = GAMEOVER

        elif kind == protocol.MSG_REMATCH:
            if self.client is not None:
                self.client.begin_run()

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
