"""Lobby: connected players, skin picker, ready flags, host Start button.

One screen serves all three modes. The host builds the roster from its own
client table; a client renders whatever the last ``lobby`` message said; local
co-op shows a fixed two-player list.
"""

from __future__ import annotations

from kivy.app import App
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.utils import platform

from game import constants as C
from net import protocol
from ui import settings, theme
from ui.widgets import (Badge, Caption, Card, Dot, MenuButton, Panel, Title)

from . import GAME, MENU


class LobbyScreen(Screen):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._ready = False

        root = Panel()
        root.add_widget(Title("Lobby", theme.FONT_HEADING))

        self.subtitle = Caption("", theme.FONT_SMALL)
        root.add_widget(self.subtitle)

        # The host's address, big enough to read out loud across a room. It is
        # the one thing a host has to tell everybody, and it used to be buried
        # mid-sentence in the subtitle.
        self.address_card = Card(fill=theme.SURFACE_ALT, outline=(0, 0, 0, 0),
                                 auto_height=False, size_hint_y=None, height=0,
                                 opacity=0.0)
        self.address_caption = Caption("OTHERS JOIN AT", theme.FONT_CAPTION,
                                       color=theme.FAINT)
        self.address_value = Caption("", theme.FONT_HEADING, color=theme.FG)
        self.address_card.add_widget(self.address_caption)
        self.address_card.add_widget(self.address_value)
        root.add_widget(self.address_card)

        # Scrollable: four players plus buttons do not fit the short axis of a
        # phone held in landscape.
        # Fixed height: the panel's column is content-sized, so a size_hint of
        # 1 here would collapse the list to nothing.
        # Grows with the roster but never past four rows, after which it
        # scrolls. A fixed height would leave a dead gap with two players.
        self.player_scroll = ScrollView(bar_width=dp(4), size_hint_y=None,
                                        height=theme.ROW_HEIGHT,
                                        do_scroll_x=False)
        self.player_list = BoxLayout(orientation="vertical", spacing=theme.GAP_SM,
                                     size_hint_y=None)
        self.player_list.bind(minimum_height=self._sync_list_height)
        self.player_scroll.add_widget(self.player_list)
        root.add_widget(self.player_scroll)

        self.skin_button = MenuButton("Dino: Rex", self._cycle_skin,
                                      variant="secondary",
                                      subtitle="tap to change")
        root.add_widget(self.skin_button)

        self.ready_button = MenuButton("I'm Ready", self._toggle_ready)
        root.add_widget(self.ready_button)

        self.start_button = MenuButton("Start Game", self._start)
        root.add_widget(self.start_button)

        root.add_widget(MenuButton("Leave", self._back, variant="quiet",
                                   height=theme.BUTTON_HEIGHT_SMALL))
        self.add_widget(root)

    # -- lifecycle ----------------------------------------------------------

    def on_pre_enter(self, *_args) -> None:
        app = App.get_running_app()
        self._ready = False
        host_mode = app.mode == "host"

        if host_mode:
            self.subtitle.text = "Waiting for players to join and get ready"
            self.address_value.text = f"{app.local_ip()}:{C.PORT_GAME}"
        elif app.mode == "client":
            self.subtitle.text = "Connected - waiting for the host to start"
        else:
            solo = platform in ("android", "ios")
            self.subtitle.text = ("Solo run on this device" if solo else
                                  "Two dinos, one keyboard")

        self._show_address(host_mode)
        self.refresh()

    def _show_address(self, visible: bool) -> None:
        self.address_card.opacity = 1.0 if visible else 0.0
        self.address_card.height = (
            self.address_caption.height + self.address_value.height
            + 2 * theme.CARD_PAD + theme.GAP_SM) if visible else 0

    def _sync_list_height(self, _widget, minimum_height: float) -> None:
        self.player_list.height = minimum_height
        self.player_scroll.height = min(minimum_height,
                                        (theme.ROW_HEIGHT + theme.GAP_SM) * 4.2)

    @staticmethod
    def _set_visible(widget, visible: bool, height: float) -> None:
        """Collapse a control to zero height when hidden.

        Setting opacity alone leaves the widget occupying its full height, so
        the lobby ended up with large dead gaps where the Ready or Start
        button was merely invisible.
        """
        widget.opacity = 1.0 if visible else 0.0
        widget.disabled = not visible
        widget.height = height if visible else 0
        widget.size_hint_y = None

    def refresh(self) -> None:
        """Rebuild the roster view. Safe to call from the App's net pump."""
        app = App.get_running_app()
        if app is None:
            return

        self.player_list.clear_widgets()
        for entry in self._entries(app):
            self.player_list.add_widget(self._row(entry, app))

        skin_name = C.SKIN_NAMES[app.skin % len(C.SKIN_NAMES)]
        self.skin_button.text = self.skin_button.compose(
            f"Dino: {skin_name}", "tap to change")

        is_host = app.mode == "host"
        is_client = app.mode == "client"

        self._set_visible(self.ready_button, is_client, theme.BUTTON_HEIGHT)
        self.ready_button.text = ("Ready - tap to cancel" if self._ready
                                  else "I'm Ready")

        self._set_visible(self.start_button, not is_client,
                          theme.BUTTON_HEIGHT)
        if is_host:
            everyone = app.host.everyone_ready() if app.host else True
            count = app.host.player_count() if app.host else 1
            self.start_button.disabled = not everyone
            if everyone:
                self.start_button.text = (f"Start Game ({count} player"
                                          f"{'' if count == 1 else 's'})")
            else:
                waiting = max(0, count - 1)
                self.start_button.text = (f"Waiting for {waiting} player"
                                          f"{'' if waiting == 1 else 's'}...")

    def _entries(self, app) -> list[dict]:
        if app.mode == "host" and app.host is not None:
            return app.host.roster()
        if app.mode == "client":
            return app.lobby_players
        if platform in ("android", "ios"):
            # Touch drives one dino; a second local dino would just stand
            # there and kill the team on the first cactus.
            return [{"id": 0, "name": app.player_name, "skin": app.skin,
                     "ready": True, "host": True}]
        return [
            {"id": 0, "name": app.player_name, "skin": app.skin,
             "ready": True, "host": True},
            {"id": 1, "name": "Player 2", "skin": (app.skin + 1) % len(
                C.SKIN_NAMES), "ready": True, "host": False},
        ]

    def _row(self, entry: dict, app) -> BoxLayout:
        """One roster line: colour dot, name, role badges, ready state.

        Badges rather than the old run-on string ("Rex - host, you - READY"):
        with four players that line was unreadable, and the ready flag -- the
        only part anybody is actually scanning for -- was last.
        """
        skin_index = int(entry.get("skin", 0))
        color = C.SKIN_COLORS[skin_index % len(C.SKIN_COLORS)]
        ready = bool(entry.get("ready"))

        row = Card(orientation="horizontal", auto_height=False,
                   size_hint_y=None, height=theme.ROW_HEIGHT,
                   padding=(theme.PAD, 0), spacing=theme.GAP,
                   fill=theme.SURFACE, outline=theme.BORDER)

        row.add_widget(Dot(color=color, pos_hint={"center_y": 0.5}))

        name = Label(text=str(entry.get("name", "Player")),
                     font_size=theme.FONT_SMALL, color=theme.FG,
                     halign="left", valign="middle", shorten=True,
                     shorten_from="right")
        name.bind(size=lambda w, *_: setattr(w, "text_size",
                                             (w.width, w.height)))
        row.add_widget(name)

        for text in self._tags(entry, app):
            row.add_widget(Badge(text, color=theme.GROUND,
                                 pos_hint={"center_y": 0.5}))

        row.add_widget(Badge("READY" if ready else "NOT READY",
                             color=theme.ACCENT if ready else theme.FAINT,
                             filled=ready, pos_hint={"center_y": 0.5}))
        return row

    @staticmethod
    def _tags(entry: dict, app) -> list[str]:
        tags = []
        if entry.get("host"):
            tags.append("HOST")
        if app.mode == "client" and app.client is not None:
            if entry.get("id") == app.client.player_id:
                tags.append("YOU")
        elif app.mode == "host" and entry.get("id") == 0:
            tags.append("YOU")
        return tags

    # -- actions ------------------------------------------------------------

    def _cycle_skin(self) -> None:
        app = App.get_running_app()
        app.skin = (app.skin + 1) % len(C.SKIN_NAMES)
        settings.set_value("skin", app.skin)
        if app.mode == "client" and app.client is not None:
            app.client.send(protocol.skin(app.skin))
        elif app.mode == "host" and app.host is not None:
            app.host.host_skin = app.skin
            app.host.push_lobby()
        self.refresh()

    def _toggle_ready(self) -> None:
        app = App.get_running_app()
        if app.mode != "client" or app.client is None:
            return
        self._ready = not self._ready
        app.client.send(protocol.ready(self._ready))
        self.refresh()

    def _start(self) -> None:
        app = App.get_running_app()
        if app.mode == "host" and app.host is not None:
            app.host.start_game()
        self.manager.current = GAME

    def _back(self) -> None:
        App.get_running_app().leave_network()
        self.manager.current = MENU
