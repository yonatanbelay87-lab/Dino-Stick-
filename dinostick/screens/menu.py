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
from ui import fonts, highscores, settings, theme
from ui import format as fmt
from ui.widgets import (Caption, Card, Chip, DashedCard, Dialog, DinoAvatar,
                        Divider, GameTitle, MenuButton, Panel, TextLink,
                        TouchButton, hug)

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
        self._status = Caption("Searching...", theme.FONT_SMALL,
                               color=theme.FAINT)
        self.add_widget(self._status)

        scroll = ScrollView(bar_width=theme.SCROLLBAR_WIDTH, do_scroll_x=False)
        holder = BoxLayout(orientation="vertical", size_hint_y=None,
                           spacing=theme.GAP)
        holder.bind(minimum_height=holder.setter("height"))

        self._list = BoxLayout(orientation="vertical", size_hint_y=None,
                               spacing=theme.GAP_SM)
        self._list.bind(minimum_height=self._list.setter("height"))
        holder.add_widget(self._list)

        # The empty state lives where the game cards will be, so the dialog
        # never shows a blank hole. Collapsed to zero once games turn up.
        self._empty = DashedCard(size_hint_y=None, height=0, opacity=0.0)
        self._empty_label = Label(
            text="", font_size=theme.FONT_SMALL,
            font_name=theme.FONT_BODY_NAME, color=theme.FAINT,
            halign="center", valign="middle")
        self._empty_label.bind(size=lambda w, *_: setattr(
            w, "text_size", (w.width, None)))
        self._empty.add_widget(self._empty_label)
        holder.add_widget(self._empty)

        scroll.add_widget(holder)
        self.add_widget(scroll)

        # Manual entry stays visible from the start rather than hiding behind
        # an "advanced" toggle: on a locked-down network it is the only way in,
        # and a player who cannot find it assumes the game is broken.
        manual = BoxLayout(orientation="horizontal", size_hint_y=None,
                           height=theme.BUTTON_HEIGHT_SMALL, spacing=theme.GAP)
        manual.add_widget(Label(text="Address", font_size=theme.FONT_SMALL,
                                font_name=theme.FONT_BODY_NAME,
                                color=theme.GROUND, size_hint_x=None,
                                width=dp(80)))
        # input_type="number" asks Android for the numeric keypad rather than
        # the full QWERTY one, which is what you want for an IP address.
        # input_type is "text", NOT "number", however wrong that looks for an
        # IP address. Android's numeric keypad has no "." key on most
        # keyboards, so the one fallback that works when broadcast is blocked
        # was literally untypeable on a phone.
        #
        # background_normal="" drops Kivy's bevelled input bitmap for a flat
        # fill, so the field matches the cards around it.
        # Mono, because this is an address: a proportional font puts 1 and l
        # and 0 and O close enough that reading one out across a room goes
        # wrong, which is exactly when this field gets used.
        self._ip = TextInput(text=settings.get("last_address") or "192.168.1.",
                             multiline=False,
                             font_size=theme.FONT_BODY,
                             font_name=theme.FONT_MONO_NAME,
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
            self._empty.height = 0
            self._empty.opacity = 0.0
        elif self._elapsed > DISCOVERY_PATIENCE:
            # Broadcast is blocked on plenty of networks, and on Android it
            # needs a multicast lock. Point at the fallback rather than
            # leaving the player staring at a spinner forever.
            self._status.text = "Nothing found yet"
            self._show_empty("No games found on this Wi-Fi yet...\n"
                             "Ask the host to read out the address from "
                             "their lobby, and type it in below.")
        else:
            self._status.text = "Searching..."
            self._show_empty("Looking for games on this Wi-Fi...")

    def _show_empty(self, message: str) -> None:
        """An empty list is the state you look at MOST while joining.

        Leaving it blank reads as a broken screen, so the space the game cards
        will occupy says what is happening and what to do instead.
        """
        self._empty_label.text = message
        self._empty.opacity = 1.0
        self._empty.height = max(theme.ROW_HEIGHT * 1.6,
                                 self._empty_label.texture_size[1]
                                 + 2 * theme.CARD_PAD)

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
            font_name=theme.FONT_DISPLAY_NAME,
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

        scroll = ScrollView(bar_width=theme.SCROLLBAR_WIDTH, do_scroll_x=False)
        column = BoxLayout(orientation="vertical", spacing=theme.GAP,
                           size_hint_y=None, padding=(0, 0, theme.GAP, 0))
        column.bind(minimum_height=column.setter("height"))

        for heading, body in self.SECTIONS:
            card = Card(fill=theme.SURFACE_ALT, outline=theme.BORDER)
            card.add_widget(Caption(heading, theme.FONT_BODY, color=theme.FG,
                                    halign="left"))
            text = Label(text=body, font_size=theme.FONT_SMALL,
                         font_name=theme.FONT_BODY_NAME,
                         color=theme.GROUND, size_hint_y=None, halign="left",
                         valign="top")
            text.bind(width=lambda w, *_: setattr(w, "text_size",
                                                  (w.width, None)),
                      texture_size=lambda w, s: setattr(w, "height", s[1]))
            card.add_widget(text)
            column.add_widget(card)

        scroll.add_widget(column)
        self.add_widget(scroll)


# ---------------------------------------------------------------------------
# Who built this  --  EDIT ME
# ---------------------------------------------------------------------------
#
# The three people who built Dino Stick, and nobody else.
#
#   name     shown large
#   role     one short line -- what this person did
#   skin     index into constants.SKINS, so each builder gets their own dino
#   bio      OPTIONAL, one or two sentences; the card is small on purpose
#   contact  OPTIONAL, shown in mono and copyable, never opened as a link
#
# `bio` and `contact` are left off deliberately rather than invented -- the
# card hides any field that is absent, so adding a line per person later is
# just a matter of filling one in. Add or remove entries freely; the dialog
# lays out whatever is here.
BUILDERS: tuple[dict, ...] = (
    {"name": "Yonatan Belay", "role": "Developer", "skin": 1},
    {"name": "Mikiyas Dawit", "role": "Developer", "skin": 3},
    {"name": "Abenezer Dawit", "role": "Developer", "skin": 4},
)

# Shown under the builders. Unlike the block above, this part is true.
TECH_NOTES = (
    "Python and Kivy, no game engine.",
    "Every dino, cactus and canyon is drawn in code -- no sprite sheets.",
    "Multiplayer runs over your own Wi-Fi or hotspot. No servers, no "
    "accounts, no internet needed.",
)


class CreditsDialog(Dialog):
    """Who made this. Entirely self-contained -- nothing is fetched.

    A "developer link" on a phone usually means a URL, which would mean the
    credits only work with a signal. This game is built to run on a hotspot in
    a field, so the profiles live in the app and a contact address is shown as
    copyable text rather than something that launches a browser.
    """

    def __init__(self, **kw) -> None:
        super().__init__(title="Who made this", dismiss_text="Back", **kw)

        scroll = ScrollView(bar_width=theme.SCROLLBAR_WIDTH, do_scroll_x=False)
        column = BoxLayout(orientation="vertical", spacing=theme.SPACE_3,
                           size_hint_y=None, padding=(0, 0, theme.SPACE_2, 0))
        column.bind(minimum_height=column.setter("height"))

        for person in BUILDERS:
            column.add_widget(self._person_card(person))

        column.add_widget(Divider())
        notes = Label(
            text="\n".join(f"-  {line}" for line in TECH_NOTES),
            font_size=theme.FONT_SMALL, font_name=theme.FONT_BODY_NAME,
            color=theme.FAINT, size_hint_y=None, halign="left", valign="top")
        notes.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)),
                   texture_size=lambda w, s: setattr(w, "height", s[1]))
        column.add_widget(notes)

        # Type credit. The OFL does not demand it in-app, but naming the fonts
        # is good manners and it doubles as a diagnostic: if the .ttf files are
        # missing this line says so instead of quietly lying about the look.
        column.add_widget(Caption(self._font_line(), theme.FONT_CAPTION,
                                  color=theme.FAINT, halign="left"))

        scroll.add_widget(column)
        self.add_widget(scroll)

    @staticmethod
    def _font_line() -> str:
        wanted = "Fredoka, Nunito and Space Mono (SIL Open Font License)"
        if all(fonts.loaded.get(role, False)
               for role in (fonts.DISPLAY, fonts.BODY, fonts.MONO)):
            return f"Type: {wanted}."
        return (f"Type: {wanted} -- not installed, so this is Kivy's Roboto. "
                "See assets/fonts/README.md.")

    @staticmethod
    def _person_card(person: dict) -> Card:
        card = Card(fill=theme.SURFACE_ALT, outline=(0, 0, 0, 0),
                    orientation="horizontal", spacing=theme.SPACE_3)

        # A dino per builder, from the game's own skin data -- same portrait
        # treatment as the lobby roster, so a credit reads as a player.
        avatar = DinoAvatar(int(person.get("skin", 0)),
                            size=(dp(48), dp(48)),
                            pos_hint={"top": 1})
        card.add_widget(avatar)

        text = BoxLayout(orientation="vertical", spacing=theme.SPACE_1)
        text.bind(minimum_height=text.setter("height"))

        name = Label(text=str(person.get("name", "")),
                     font_size=theme.FONT_BODY,
                     font_name=theme.FONT_DISPLAY_NAME, color=theme.FG,
                     size_hint_y=None, height=theme.FONT_BODY * 1.4,
                     halign="left", valign="middle")
        name.bind(size=lambda w, *_: setattr(w, "text_size",
                                             (w.width, w.height)))
        text.add_widget(name)

        role = person.get("role")
        if role:
            text.add_widget(Caption(role, theme.FONT_CAPTION,
                                    color=theme.ACCENT, halign="left"))

        bio = person.get("bio")
        if bio:
            body = Label(text=bio, font_size=theme.FONT_SMALL,
                         font_name=theme.FONT_BODY_NAME, color=theme.GROUND,
                         size_hint_y=None, halign="left", valign="top")
            body.bind(width=lambda w, *_: setattr(w, "text_size",
                                                  (w.width, None)),
                      texture_size=lambda w, s: setattr(w, "height", s[1]))
            text.add_widget(body)

        contact = person.get("contact")
        if contact:
            text.add_widget(Caption(contact, theme.FONT_SMALL,
                                    color=theme.FAINT, mono=True,
                                    halign="left"))

        card.add_widget(text)
        # The card is a row, so it cannot measure itself from its own children
        # the way a vertical Card does.
        card.size_hint_y = None
        text.bind(height=lambda _w, value: setattr(
            card, "height", max(value, avatar.height) + 2 * theme.CARD_PAD))
        return card


class HighScoreCard(Card):
    """Your best runs on this device, sat directly above PLAY.

    ONE row, and that is not a stylistic preference -- it is the only thing
    that fits. The right column already spends about 240dp on four buttons
    against roughly 340dp of safe height on a landscape phone, which leaves
    almost no slack. Measured: a stacked version (heading, big number,
    runners-up on their own lines) came to 134px and forced the whole column
    to scroll on every phone size up to 780x360. As a single row it is 54px
    and the column still fits without scrolling.

    So: the best score reads large on the left, the next two ride along as
    small faint text on the right, and the whole thing costs one line.

    Collapses to zero height until there is a score to show. A card reading
    "no scores yet" above the Play button is furniture in the way of the one
    thing a new player is trying to do.
    """

    def __init__(self, **kw) -> None:
        super().__init__(fill=theme.SURFACE_ALT, outline=theme.BORDER,
                         auto_height=False, size_hint_y=None, height=0,
                         opacity=0.0, orientation="horizontal",
                         padding=(theme.SPACE_3, theme.SPACE_2),
                         spacing=theme.SPACE_2, **kw)

        self._caption = Caption("BEST", theme.FONT_CAPTION, color=theme.FAINT,
                                halign="left", size_hint_x=None)
        self._caption.width = dp(40)
        self.add_widget(self._caption)

        # The headline number, in the accent the game uses for score
        # everywhere else, so the menu and the game-over card agree.
        self._best = Label(text="0", font_size=theme.FONT_BODY,
                           font_name=theme.FONT_DISPLAY_NAME,
                           color=theme.ACCENT, size_hint_x=None,
                           halign="left", valign="middle")
        self._best.bind(texture_size=lambda w, s: setattr(w, "width", s[0]))
        self.add_widget(self._best)

        # Second and third, pushed to the far end: present, clearly secondary,
        # and costing no extra height.
        self._runners = Label(text="", font_size=theme.FONT_CAPTION,
                              font_name=theme.FONT_BODY_NAME,
                              color=theme.FAINT, halign="right",
                              valign="middle", shorten=True,
                              shorten_from="right")
        self._runners.bind(size=lambda w, *_: setattr(
            w, "text_size", (w.width, w.height)))
        self.add_widget(self._runners)

    def refresh(self) -> None:
        """Read the table and re-render. Cheap: it is at most three rows."""
        rows = highscores.top()
        if not rows:
            self.opacity = 0.0
            self.height = 0
            self.disabled = True
            return

        first = rows[0]
        self._best.text = fmt.score(int(first.get("score", 0)))

        # The supporting detail: how far the best run got, then the runners-up.
        # Distance is what makes a score mean something.
        bits = [fmt.distance(float(first.get("distance", 0.0)))]
        for row in rows[1:]:
            bits.append(fmt.score(int(row.get("score", 0))))
        self._runners.text = "   -   ".join(bits)

        self.opacity = 1.0
        self.disabled = False
        self.height = max(theme.ROW_HEIGHT_COMPACT,
                          theme.FONT_BODY * 1.4 + 2 * theme.SPACE_2)


class MenuScreen(Screen):
    """Two columns: identity on the left, the three ways to play on the right.

    Stacked in one column this screen was ~450dp tall and a landscape phone
    offers about 340dp of safe height, so it scrolled -- on the very first
    screen of the game. Side by side it fits on the smallest phone with room
    to spare, and the eye gets a clear left-to-right read: who you are, then
    what to do.
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        root = Panel(columns=2)

        # -- left: the wordmark, the pitch, who you are ---------------------
        root.col_left.add_widget(GameTitle())
        root.col_left.add_widget(Caption(
            "Co-op endless runner\nOne rope. Shared fate.",
            theme.FONT_SMALL))

        # Collapsed to zero height until there is something to say -- vertical
        # space on a landscape phone is too scarce to reserve for an empty row.
        self.error_card = Card(fill=(*theme.DANGER[:3], 0.20),
                               outline=(*theme.DANGER[:3], 0.55),
                               auto_height=False, size_hint_y=None, height=0,
                               opacity=0.0)
        self.error_label = Label(
            text="", font_size=theme.FONT_SMALL,
            font_name=theme.FONT_BODY_NAME, color=theme.CREAM,
            halign="center", valign="middle")
        self.error_label.bind(size=lambda w, *_: setattr(
            w, "text_size", (w.width, w.height)))
        self.error_card.add_widget(self.error_label)
        root.col_left.add_widget(self.error_card)

        # A chip, not a button: this is a status you may edit, and giving it a
        # candy slab would put a third emphasis level next to Play.
        self.name_button = Chip("", self._edit_name)
        root.col_left.add_widget(hug(self.name_button))
        # Doubles as the version line. A separate credits row would have cost
        # another 48dp of touch target on the one screen with the least
        # vertical room to spare, for a link almost nobody taps twice.
        root.col_left.add_widget(TextLink(
            f"v{C.APP_VERSION}  -  who made this", self._credits))

        # -- right: one primary, two alternatives, one quiet ----------------
        #
        # One candy-green button, because there is one obvious thing to do
        # first. Everything else is cream, so the eye lands on "Play" instead
        # of scanning three identical green slabs.
        #
        # The subtitle tells the truth per platform: touch drives one dino, a
        # keyboard drives two.
        # Your best runs, immediately above PLAY: it is the reason to press it.
        self.high_scores = HighScoreCard()
        root.col_right.add_widget(self.high_scores)

        solo = platform in ("android", "ios")
        root.col_right.add_widget(MenuButton(
            "PLAY", self._local,
            subtitle=("Solo run on this device" if solo
                      else "Two dinos, one keyboard")))
        root.col_right.add_widget(MenuButton(
            "HOST", self._host, variant="secondary",
            subtitle="Friends join from your Wi-Fi"))
        root.col_right.add_widget(MenuButton(
            "JOIN", self._join, variant="secondary",
            subtitle="Find a game on this network"))
        root.col_right.add_widget(MenuButton("How to Play", self._how,
                                         variant="quiet",
                                         height=theme.BUTTON_HEIGHT_SMALL))

        self.add_widget(root)
        self._sync_name()
        # Populated on launch from the table the app loaded during build(), so
        # the card is correct on the very first frame rather than filling in a
        # moment later.
        self.high_scores.refresh()

    def on_pre_enter(self, *_args) -> None:
        # Coming back from a game: make sure nothing is still connected.
        app = App.get_running_app()
        if app is not None:
            app.leave_network()
        self._sync_name()
        # A run just ended, so this is where a new record shows up.
        self.high_scores.refresh()

    # -- player name --------------------------------------------------------

    def _sync_name(self) -> None:
        app = App.get_running_app()
        name = app.player_name if app is not None else settings.get(
            "player_name")
        self.name_button.text = f"playing as [b]{name}[/b]  -  tap to change"
        self.name_button.markup = True

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
        app.ensure_screens()
        if app.start_hosting():
            self.manager.current = LOBBY

    def _join(self) -> None:
        JoinDialog(on_join=self._do_join).open()

    def _do_join(self, ip: str, port: int) -> None:
        # Straight to the lobby, on this tap. The connection is dialled on a
        # worker thread and the lobby shows its progress -- see
        # DinoStickApp.join_game. Waiting here for the socket is what used to
        # freeze the UI, and the frozen frames were the ones the player spent
        # jabbing at Ready.
        app = App.get_running_app()
        app.ensure_screens()
        if app.join_game(ip, port):
            self.manager.current = LOBBY

    def _local(self) -> None:
        app = App.get_running_app()
        app.ensure_screens()
        app.start_local()
        self.manager.current = LOBBY

    def _how(self) -> None:
        HowToDialog().open()

    def _credits(self) -> None:
        CreditsDialog().open()
