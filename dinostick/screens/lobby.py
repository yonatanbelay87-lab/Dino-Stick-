"""Lobby: connected players, skin picker, ready flags, host Start button.

One screen serves all three modes. The host builds the roster from its own
client table; a client renders whatever the last ``lobby`` message said; local
co-op shows a fixed two-player list.
"""

from __future__ import annotations

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.utils import platform

from game import constants as C
from net import protocol
from ui import settings, theme
from ui.widgets import (Badge, Caption, Card, DashedCard, DinoAvatar,
                        DinoPicker, IconButton, LiveDot, MenuButton, Panel,
                        Title, animate)

from . import GAME, MENU


class LobbyScreen(Screen):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._ready = False
        # Player ids rendered last time, so refresh() can tell an arrival
        # from a re-render.
        self._seen_ids: set = set()

        # Top-aligned, not centred. The two halves hold very different amounts
        # -- four roster rows on the left against three controls on the right
        # -- and centring each one independently started them at two unrelated
        # heights, which is what made the screen read as two screens.
        root = Panel(columns=2, align="top")

        # -- left: who is here ----------------------------------------------
        #
        # Title and subtitle are ONE group with a hairline gap, then a normal
        # gap to the content below. Spaced evenly they read as three unrelated
        # lines; grouped, the eye takes the heading and its explanation in one
        # go and moves on.
        heading = BoxLayout(orientation="vertical", size_hint_y=None,
                            spacing=theme.SPACE_1)
        header = BoxLayout(orientation="horizontal", size_hint_y=None,
                           height=theme.FONT_HEADING * 1.5,
                           spacing=theme.SPACE_2)
        self.live_dot = LiveDot(pos_hint={"center_y": 0.5})
        header.add_widget(self.live_dot)
        header.add_widget(Title("LOBBY", theme.FONT_HEADING, halign="left"))
        heading.add_widget(header)

        self.subtitle = Caption("", theme.FONT_SMALL, halign="left")
        heading.add_widget(self.subtitle)
        heading.bind(minimum_height=heading.setter("height"))
        root.col_left.add_widget(heading)

        # The host's address, big enough to read out loud across a room. It is
        # the one thing a host has to tell everybody, and it used to be buried
        # mid-sentence in the subtitle. Mono so 1/l and 0/O cannot be confused
        # by whoever is typing it in at the other end.
        self.address_card = Card(fill=theme.SURFACE_ALT, outline=theme.BORDER,
                                 auto_height=False, size_hint_y=None, height=0,
                                 opacity=0.0, orientation="horizontal",
                                 padding=(theme.SPACE_3, theme.SPACE_2),
                                 spacing=theme.SPACE_2)
        address_text = BoxLayout(orientation="vertical", spacing=0)
        self.address_caption = Caption("OTHERS JOIN AT", theme.FONT_CAPTION,
                                       color=theme.FAINT, halign="left")
        # FONT_BODY, not FONT_HEADING. At 24sp the address ran most of the
        # way across the column on a 640dp phone and dwarfed the heading
        # above it. 18sp mono is still readable out loud across a room,
        # which is the only job this line has.
        self.address_value = Caption("", theme.FONT_BODY, color=theme.FG,
                                     mono=True, halign="left")
        address_text.add_widget(self.address_caption)
        address_text.add_widget(self.address_value)
        self.address_card.add_widget(address_text)
        self.copy_button = IconButton("copy", self._copy_address,
                                      variant="quiet",
                                      pos_hint={"center_y": 0.5})
        self.address_card.add_widget(self.copy_button)
        root.col_left.add_widget(self.address_card)

        # Grows with the roster but never past four rows, after which it
        # scrolls. A fixed height would leave a dead gap with two players.
        self.player_scroll = ScrollView(bar_width=theme.SCROLLBAR_WIDTH, size_hint_y=None,
                                        height=theme.ROW_HEIGHT_COMPACT,
                                        do_scroll_x=False)
        # 8dp between rows, not 4. At 4 the outlines of adjacent cards nearly
        # touched and the list read as one striped block; 8 lets each player be
        # their own card without the stack turning busy.
        self.player_list = BoxLayout(orientation="vertical",
                                     spacing=theme.SPACE_2,
                                     size_hint_y=None)
        self.player_list.bind(minimum_height=self._sync_list_height)
        self.player_scroll.add_widget(self.player_list)
        root.col_left.add_widget(self.player_scroll)

        # -- right: your dino, and the way in -------------------------------
        self.dino_picker = DinoPicker(self._set_skin)
        root.col_right.add_widget(self.dino_picker)

        self.ready_button = MenuButton("I'M READY", self._toggle_ready,
                                       height=theme.BUTTON_HEIGHT_COMPACT)
        root.col_right.add_widget(self.ready_button)

        self.start_button = MenuButton("START GAME", self._start,
                                       height=theme.BUTTON_HEIGHT_COMPACT)
        root.col_right.add_widget(self.start_button)

        root.col_right.add_widget(MenuButton(
            "Leave", self._back, variant="quiet",
            height=theme.BUTTON_HEIGHT_SMALL))

        # Kept as an alias: refresh() retitles it, and the old name is part of
        # the screen's surface. The picker owns the label now.
        self.skin_button = self.dino_picker
        self.add_widget(root)

    # -- lifecycle ----------------------------------------------------------

    def on_pre_enter(self, *_args) -> None:
        app = App.get_running_app()
        self._ready = False
        # Arriving fresh: everyone already here is not an "arrival".
        self._seen_ids = {e.get("id") for e in self._entries(app)}
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
        self.live_dot.start_pulse()
        self.refresh()

    def on_leave(self, *_args) -> None:
        # A looping Animation on a screen nobody can see is pure waste.
        self.live_dot.stop_pulse()

    def _show_address(self, visible: bool) -> None:
        self.address_card.opacity = 1.0 if visible else 0.0
        self.address_card.disabled = not visible
        # Tall enough for the copy button as well as the two lines of text,
        # since the card is a row now rather than a stack.
        self.address_card.height = max(
            self.address_caption.height + self.address_value.height
            + 2 * theme.CARD_PAD,
            self.copy_button.height + 2 * theme.SPACE_2) if visible else 0

    def _sync_list_height(self, _widget, minimum_height: float) -> None:
        self.player_list.height = minimum_height
        self.player_scroll.height = min(
            minimum_height,
            (theme.ROW_HEIGHT_COMPACT + theme.SPACE_2) * 4.2)

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

        # refresh() rebuilds the whole list on every lobby message, so a
        # blanket entrance animation would re-play for everyone whenever
        # anybody toggled Ready. Diff by id instead: only a genuinely new
        # arrival fades in, which is the moment worth drawing attention to.
        entries = self._entries(app)
        seen = {entry.get("id") for entry in entries}
        arrivals = seen - self._seen_ids
        self._seen_ids = seen

        self.player_list.clear_widgets()
        for entry in entries:
            row = self._row(entry, app)
            if entry.get("id") in arrivals:
                row.opacity = 0.0
                animate(row, theme.T_ENTER, theme.EASE_IN, opacity=1.0)
            self.player_list.add_widget(row)

        # A dashed placeholder for the seat nobody is in yet. Hosting alone
        # with a bare list looks like the lobby failed to load; a slot that
        # says it is waiting looks like a lobby.
        if app.mode == "host" and len(entries) < C.MAX_PLAYERS:
            self.player_list.add_widget(self._open_slot(len(entries) + 1))

        self.dino_picker.set_skin(app.skin)

        is_host = app.mode == "host"
        is_client = app.mode == "client"

        self._set_visible(self.ready_button, is_client,
                          theme.BUTTON_HEIGHT_COMPACT)
        self.ready_button.text = ("READY - tap to cancel" if self._ready
                                  else "I'M READY")

        self._set_visible(self.start_button, not is_client,
                          theme.BUTTON_HEIGHT_COMPACT)
        if is_host:
            everyone = app.host.everyone_ready() if app.host else True
            count = app.host.player_count() if app.host else 1
            self.start_button.disabled = not everyone
            if everyone:
                self.start_button.text = (f"START GAME ({count} player"
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
        ready = bool(entry.get("ready"))

        # No outline. Four outlined cards in a stack put four hairlines within
        # 8dp of each other and the whole list buzzed; the fill alone separates
        # a row from the sky perfectly well, and dropping the stroke is most of
        # what makes the column feel calm.
        row = Card(orientation="horizontal", auto_height=False,
                   size_hint_y=None, height=theme.ROW_HEIGHT_COMPACT,
                   padding=(theme.SPACE_2, theme.SPACE_1),
                   spacing=theme.SPACE_2,
                   fill=theme.SURFACE, outline=(0, 0, 0, 0))

        # The player's actual dino, not a coloured dot. Same data the renderer
        # uses, so the avatar and the character you run as cannot disagree.
        row.add_widget(DinoAvatar(
            skin_index,
            size=(theme.snap(theme.ROW_HEIGHT_COMPACT * 0.74),) * 2,
            pos_hint={"center_y": 0.5}))

        name = Label(text=str(entry.get("name", "Player")),
                     font_size=theme.FONT_SMALL,
                     font_name=theme.FONT_BODY_NAME, color=theme.FG,
                     halign="left", valign="middle", shorten=True,
                     shorten_from="right")
        name.bind(size=lambda w, *_: setattr(w, "text_size",
                                             (w.width, w.height)))
        row.add_widget(name)

        for text in self._tags(entry, app):
            row.add_widget(Badge(text, color=theme.GROUND,
                                 pos_hint={"center_y": 0.5}))

        row.add_widget(Badge("READY" if ready else "WAITING",
                             color=theme.ACCENT if ready else theme.FAINT,
                             filled=ready, pos_hint={"center_y": 0.5}))
        return row

    @staticmethod
    def _open_slot(number: int) -> DashedCard:
        """The seat nobody is in yet."""
        slot = DashedCard(size_hint_y=None,
                          height=theme.ROW_HEIGHT_COMPACT,
                          padding=(theme.SPACE_3, theme.SPACE_1),
                          orientation="horizontal")
        label = Label(text=f"Open slot  -  waiting for player {number}...",
                      font_size=theme.FONT_SMALL,
                      font_name=theme.FONT_BODY_NAME, color=theme.FAINT,
                      halign="left", valign="middle")
        label.bind(size=lambda w, *_: setattr(w, "text_size",
                                              (w.width, w.height)))
        slot.add_widget(label)
        return slot

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

    def _copy_address(self) -> None:
        """Put the host address on the clipboard.

        Kivy's Clipboard is a local system call, not a network one -- it is the
        same API the OS uses for any copy/paste. The label doubles as the
        confirmation so there is no toast to time out.
        """
        text = self.address_value.text
        if not text:
            return
        try:
            from kivy.core.clipboard import Clipboard  # noqa: PLC0415

            Clipboard.copy(text)
        except Exception:
            # No clipboard provider (a bare-bones Linux box, mostly). The
            # address is still on screen to read out, so say nothing.
            return
        self.address_caption.text = "COPIED!"
        Clock.schedule_once(
            lambda _dt: setattr(self.address_caption, "text",
                                "OTHERS JOIN AT"), 1.6)

    def _set_skin(self, index: int) -> None:
        """Pick a specific dino. The arrow picker calls this."""
        app = App.get_running_app()
        app.skin = index % len(C.SKIN_NAMES)
        settings.set_value("skin", app.skin)
        if app.mode == "client" and app.client is not None:
            app.client.send(protocol.skin(app.skin))
        elif app.mode == "host" and app.host is not None:
            app.host.host_skin = app.skin
            app.host.push_lobby()
        self.refresh()

    def _cycle_skin(self) -> None:
        """Step to the next dino. Kept: it is part of this screen's surface."""
        app = App.get_running_app()
        self._set_skin(app.skin + 1)

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
