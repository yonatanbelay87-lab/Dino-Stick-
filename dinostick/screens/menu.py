"""Main menu: Play / Host / Join / How to Play.

The join dialog always offers manual IP entry alongside the discovered-games
list, because UDP broadcast is blocked on plenty of networks and needs a
multicast lock on Android.
"""

from __future__ import annotations

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

from game import constants as C
from net.discovery import ClientDiscovery
from ui import settings, theme
from ui.widgets import (Caption, Card, Dialog, MenuButton, Panel, Title,
                        TouchButton)

from . import LOBBY

# How long to search before admitting broadcast is probably blocked and
# pointing at the manual-IP row instead of leaving a spinner running.
DISCOVERY_PATIENCE = 6.0


class JoinDialog(Dialog):
    """Discovered games + manual IP entry."""

    def __init__(self, on_join, **kw) -> None:
        super().__init__(title="Join a game", dismiss_text="Cancel", **kw)
        self._on_join = on_join
        self._discovery = ClientDiscovery()
        self._event = None
        self._elapsed = 0.0

        self.add_widget(Caption("Games on this Wi-Fi", theme.FONT_SMALL))

        # Status above the list, not below it: while the list is empty -- which
        # is most of the time you are looking at this -- a line underneath a
        # blank area reads as a footnote to nothing.
        self._status = Caption("Searching...", theme.FONT_TINY,
                               color=theme.FAINT)
        self.add_widget(self._status)

        scroll = ScrollView(bar_width=dp(3), do_scroll_x=False)
        self._list = BoxLayout(orientation="vertical", size_hint_y=None,
                               spacing=theme.GAP_SM)
        self._list.bind(minimum_height=self._list.setter("height"))
        scroll.add_widget(self._list)
        self.add_widget(scroll)

        # Manual entry stays visible from the start rather than hiding behind
        # an "advanced" toggle: on a locked-down network it is the only way in,
        # and a player who cannot find it assumes the game is broken.
        manual = BoxLayout(orientation="horizontal", size_hint_y=None,
                           height=theme.BUTTON_HEIGHT_SMALL, spacing=theme.GAP)
        manual.add_widget(Label(text="Address", font_size=theme.FONT_SMALL,
                                color=theme.GROUND, size_hint_x=None,
                                width=dp(72)))
        # input_type="number" asks Android for the numeric keypad rather than
        # the full QWERTY one, which is what you want for an IP address.
        # input_type is "text", NOT "number", however wrong that looks for an
        # IP address. Android's numeric keypad has no "." key on most
        # keyboards, so the one fallback that works when broadcast is blocked
        # was literally untypeable on a phone.
        #
        # background_normal="" drops Kivy's bevelled input bitmap for a flat
        # fill, so the field matches the cards around it.
        self._ip = TextInput(text=settings.get("last_address") or "192.168.1.",
                             multiline=False,
                             font_size=theme.FONT_BODY,
                             input_type="text", write_tab=False,
                             background_normal="", background_active="",
                             background_color=theme.SURFACE_ALT,
                             foreground_color=theme.FG, cursor_color=theme.FG,
                             padding=(theme.GAP, theme.GAP))
        # "Done" on the soft keyboard joins, rather than making the player
        # dismiss the keyboard to find the button hiding behind it.
        self._ip.bind(on_text_validate=lambda *_: self._join_manual())
        manual.add_widget(self._ip)
        manual.add_widget(TouchButton("Join", self._join_manual,
                                      height=theme.BUTTON_HEIGHT_SMALL,
                                      size_hint_x=None, width=dp(104)))
        self.add_widget(manual)

    def on_open(self) -> None:
        try:
            self._discovery.start()
        except OSError as exc:
            self._status.text = f"Search unavailable ({exc}) - type an address"
            return
        self._event = Clock.schedule_interval(self._refresh, 0.5)

    def on_dismiss(self) -> None:
        if self._event is not None:
            self._event.cancel()
            self._event = None
        self._discovery.stop()

    def _refresh(self, _dt: float) -> None:
        hosts = self._discovery.hosts()
        self._list.clear_widgets()
        for host in hosts:
            self._list.add_widget(TouchButton(
                host["name"],
                lambda h=host: self._join(h["ip"], h["port"]),
                variant="secondary",
                subtitle=f"{host['ip']}   -   {host['players']}/{host['max']} "
                         f"players   -   tap to join",
                height=theme.BUTTON_HEIGHT))

        self._elapsed += 0.5
        if hosts:
            self._status.text = (f"{len(hosts)} game"
                                 f"{'' if len(hosts) == 1 else 's'} found")
        elif self._elapsed > DISCOVERY_PATIENCE:
            # Broadcast is blocked on plenty of networks, and on Android it
            # needs a multicast lock. Point at the fallback rather than
            # leaving the player staring at a spinner.
            self._status.text = ("Nothing found. Ask the host for the address "
                                 "shown in their lobby.")
        else:
            self._status.text = "Searching..."

    def _join_manual(self) -> None:
        address = self._ip.text.strip()
        if not address:
            self._status.text = "Type the address shown in the host's lobby."
            return
        # Accept "1.2.3.4:50506" as well as a bare address: it is what the
        # host's lobby displays, so it is what people will copy.
        port = C.PORT_GAME
        if ":" in address:
            address, _, tail = address.partition(":")
            try:
                port = int(tail)
            except ValueError:
                port = C.PORT_GAME
        settings.set_value("last_address", address)
        self._join(address, port)

    def _join(self, ip: str, port: int) -> None:
        self.dismiss()
        self._on_join(ip, port)


class NameDialog(Dialog):
    """Set the name everyone else sees.

    Without this every host announced "Player 1's game" and every lobby row
    read "Player 1" -- with two games on one Wi-Fi you could not tell which
    was which, and in a four-player lobby nobody could find themselves.
    """

    def __init__(self, name: str, on_save, **kw) -> None:
        super().__init__(title="Your name", dismiss_text="Cancel", **kw)
        self._on_save = on_save

        self.add_widget(Caption("Shown to everyone you play with",
                                theme.FONT_SMALL))

        self._field = TextInput(
            text=name, multiline=False, font_size=theme.FONT_HEADING,
            write_tab=False, halign="center",
            background_normal="", background_active="",
            background_color=theme.SURFACE_ALT,
            foreground_color=theme.FG, cursor_color=theme.FG,
            size_hint_y=None, height=theme.BUTTON_HEIGHT,
            padding=(theme.GAP, theme.GAP))
        self._field.bind(on_text_validate=lambda *_: self._save())
        self.add_widget(self._field)

        self.add_widget(Caption(
            f"Up to {settings.MAX_NAME_LENGTH} characters",
            theme.FONT_CAPTION, color=theme.FAINT))
        self.add_widget(TouchButton("Save", self._save))
        # Every other child has a fixed height, and a vertical BoxLayout piles
        # the leftover space at the top -- which left the dialog looking
        # bottom-weighted. This soaks it up underneath instead.
        self.add_widget(Widget())

    def _save(self) -> None:
        self._on_save(settings.clean_name(self._field.text))
        self.dismiss()


class HowToDialog(Dialog):
    """The rules, as short sections instead of one wall of text."""

    SECTIONS = (
        ("The rope is real",
         "You are all tied together by an elastic rope. Jump TOGETHER: a "
         "mistimed jump drags your partners off the ground, and they cannot "
         "jump while they are in the air."),
        ("One team, one run",
         "If any one of you crashes, everybody loses. Watch the rope meter at "
         "the top -- green is slack, red means it is about to yank."),
        ("Controls",
         "Phone: tap the right half to jump, the left half to duck.\n"
         "Keyboard: SPACE / W jump, S duck. Player 2 uses UP and DOWN."),
        ("Playing together",
         "Host a game and everyone else joins from the same Wi-Fi or your "
         "phone's hotspot. No internet needed."),
    )

    def __init__(self, **kw) -> None:
        super().__init__(title="How to play", dismiss_text="Got it", **kw)

        scroll = ScrollView(bar_width=dp(3), do_scroll_x=False)
        column = BoxLayout(orientation="vertical", spacing=theme.GAP,
                           size_hint_y=None, padding=(0, 0, theme.GAP, 0))
        column.bind(minimum_height=column.setter("height"))

        for heading, body in self.SECTIONS:
            card = Card(fill=theme.SURFACE_ALT, outline=(0, 0, 0, 0))
            card.add_widget(Caption(heading, theme.FONT_SMALL, color=theme.FG,
                                    halign="left"))
            text = Label(text=body, font_size=theme.FONT_SMALL,
                         color=theme.GROUND, size_hint_y=None, halign="left",
                         valign="top")
            text.bind(width=lambda w, *_: setattr(w, "text_size",
                                                  (w.width, None)),
                      texture_size=lambda w, s: setattr(w, "height", s[1]))
            card.add_widget(text)
            column.add_widget(card)

        scroll.add_widget(column)
        self.add_widget(scroll)


class MenuScreen(Screen):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        root = Panel()
        root.add_widget(Title(C.APP_NAME))
        root.add_widget(Caption("Co-op endless runner. One rope. Shared fate."))

        # Collapsed to zero height until there is something to say -- vertical
        # space on a landscape phone is too scarce to reserve for an empty row.
        self.error_card = Card(fill=(0.98, 0.92, 0.91, 1.0),
                               outline=(*theme.DANGER[:3], 0.35),
                               auto_height=False, size_hint_y=None, height=0,
                               opacity=0.0)
        self.error_label = Label(
            text="", font_size=theme.FONT_SMALL, color=theme.DANGER,
            halign="center", valign="middle")
        self.error_label.bind(size=lambda w, *_: setattr(
            w, "text_size", (w.width, w.height)))
        self.error_card.add_widget(self.error_label)
        root.add_widget(self.error_card)

        # One filled button, because there is one obvious thing to do first.
        # Everything else is an outlined alternative, so the eye lands on
        # "Play" instead of scanning four identical green bars.
        #
        # The subtitle tells the truth per platform: touch drives one dino, a
        # keyboard drives two.
        solo = platform in ("android", "ios")
        root.add_widget(MenuButton(
            "Play", self._local,
            subtitle=("Solo run on this device" if solo
                      else "Two dinos, one keyboard")))
        root.add_widget(MenuButton("Host Game", self._host, variant="secondary",
                                   subtitle="Friends join from your Wi-Fi"))
        root.add_widget(MenuButton("Join Game", self._join, variant="secondary",
                                   subtitle="Find a game on this network"))
        # Built with a subtitle so the label is in markup mode from the start;
        # _sync_name rewrites the text and markup cannot be switched on later
        # without the tags showing up literally.
        self.name_button = MenuButton("Playing as", self._edit_name,
                                      subtitle="tap to change", variant="quiet",
                                      height=theme.BUTTON_HEIGHT_SMALL)
        root.add_widget(self.name_button)
        root.add_widget(MenuButton("How to Play", self._how, variant="quiet",
                                   height=theme.BUTTON_HEIGHT_SMALL))
        root.add_widget(Caption(f"v{C.APP_VERSION}", theme.FONT_CAPTION,
                                color=theme.FAINT))
        self.add_widget(root)
        self._sync_name()

    def on_pre_enter(self, *_args) -> None:
        # Coming back from a game: make sure nothing is still connected.
        app = App.get_running_app()
        if app is not None:
            app.leave_network()
        self._sync_name()

    # -- player name --------------------------------------------------------

    def _sync_name(self) -> None:
        app = App.get_running_app()
        name = app.player_name if app is not None else settings.get(
            "player_name")
        self.name_button.text = self.name_button.compose(
            f"Playing as {name}", "tap to change")

    def _edit_name(self) -> None:
        app = App.get_running_app()
        current = app.player_name if app is not None else settings.get(
            "player_name")
        NameDialog(current, self._set_name).open()

    def _set_name(self, name: str) -> None:
        # Cleaned here, not just in the dialog: this is where the value reaches
        # app state and disk, and a name that arrives padded or over-length
        # comes back silently different for every other player, because the
        # host truncates what it receives.
        name = settings.clean_name(name)
        app = App.get_running_app()
        if app is not None:
            app.player_name = name
        settings.set_value("player_name", name)
        self._sync_name()

    def show_error(self, text: str) -> None:
        self.error_label.text = text
        self.error_card.height = theme.ROW_HEIGHT + theme.CARD_PAD
        self.error_card.opacity = 1.0
        Clock.schedule_once(lambda _dt: self._clear_error(), 6)

    def _clear_error(self) -> None:
        self.error_label.text = ""
        self.error_card.height = 0
        self.error_card.opacity = 0.0

    def _host(self) -> None:
        app = App.get_running_app()
        if app.start_hosting():
            self.manager.current = LOBBY

    def _join(self) -> None:
        JoinDialog(on_join=self._do_join).open()

    def _do_join(self, ip: str, port: int) -> None:
        app = App.get_running_app()
        if app.join_game(ip, port):
            self.manager.current = LOBBY

    def _local(self) -> None:
        app = App.get_running_app()
        app.start_local()
        self.manager.current = LOBBY

    def _how(self) -> None:
        HowToDialog().open()
