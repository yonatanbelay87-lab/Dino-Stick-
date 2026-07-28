"""The widget kit: buttons, cards, stats, badges, dialogs, meters.

Everything is drawn on the canvas from ``theme`` -- no images, no atlases, no
kv files -- so the whole app is one flat near-white surface with rounded
corners, matching the game itself.

Screens are meant to compose these and never touch a colour directly. When
they do, the four screens drift apart, which is exactly how the first pass
ended up with three different greys for "secondary text".
"""

from __future__ import annotations

from typing import Callable

from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from . import theme
from .insets import insets


class _RoundedBackground:
    """Mixin: keep a rounded fill + hairline outline glued to the widget.

    Kivy's stock button background is a stretched bitmap with square corners.
    Drawing it ourselves is what lets every surface in the app share one
    corner radius, and it costs two canvas instructions.
    """

    def _init_background(self, fill, outline=(0, 0, 0, 0), radius=None) -> None:
        self._radius = theme.RADIUS if radius is None else radius
        with self.canvas.before:
            self._fill_color = Color(*fill)
            self._fill_rect = RoundedRectangle(radius=[self._radius])
            self._outline_color = Color(*outline)
            self._outline_line = Line(width=theme.BORDER_WIDTH)
        self.bind(pos=self._sync_background, size=self._sync_background)
        self._sync_background()

    def _sync_background(self, *_) -> None:
        self._fill_rect.pos = self.pos
        self._fill_rect.size = self.size
        # Inset by half the stroke so the outline sits inside the shape rather
        # than straddling the edge, which reads as a blurry double border.
        inset = theme.BORDER_WIDTH * 0.5
        self._outline_line.rounded_rectangle = (
            self.x + inset, self.y + inset,
            max(0.0, self.width - 2 * inset), max(0.0, self.height - 2 * inset),
            self._radius)

    def _set_fill(self, fill, outline=None) -> None:
        self._fill_color.rgba = fill
        if outline is not None:
            self._outline_color.rgba = outline


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------


class TouchButton(Button, _RoundedBackground):
    """Flat rounded button with a visible pressed state and a thumb target.

    Touch has no hover, so without an explicit pressed colour a tap gives no
    feedback at all and the control feels dead on a phone.

    Callers pick a ``variant`` (an emphasis role, see theme.BUTTON_VARIANTS)
    instead of colours. ``color_normal``/``color_pressed`` still override it,
    because a couple of call sites genuinely are one-offs.
    """

    def __init__(self, text: str, on_press_cb: Callable[[], None],
                 variant: str = "primary",
                 color_normal=None, color_pressed=None,
                 height: float | None = None,
                 subtitle: str = "", **kw) -> None:
        style = theme.BUTTON_VARIANTS.get(variant,
                                          theme.BUTTON_VARIANTS["primary"])
        self._c_normal = color_normal or style["fill"]
        self._c_pressed = color_pressed or style["down"]
        self._c_outline = style["outline"]
        self._c_text = style["text"]

        # A two-line button needs two lines' worth of room. At the 48dp small
        # height a title plus a subtitle already filled it, and a player with
        # Android's font size turned up lost the second line entirely -- so a
        # subtitle raises the floor rather than being squeezed in.
        floor = theme.BUTTON_HEIGHT if subtitle else theme.TOUCH_MIN

        super().__init__(
            text=self.compose(text, subtitle),
            markup=bool(subtitle),
            size_hint_y=None,
            height=max(height or theme.BUTTON_HEIGHT, floor),
            font_size=theme.FONT_BODY,
            halign="center",
            valign="middle",
            # The real background is drawn below; Kivy's own must be invisible
            # or it squares off our corners.
            background_normal="",
            background_down="",
            background_disabled_normal="",
            background_color=(0, 0, 0, 0),
            color=self._c_text,
            **kw,
        )
        self._init_background(self._c_normal, self._c_outline)
        self.bind(on_release=lambda *_: on_press_cb(),
                  state=self._sync_style, disabled=self._sync_style,
                  size=self._sync_text_size)
        self._sync_text_size()

    @staticmethod
    def compose(text: str, subtitle: str = "") -> str:
        """Label markup for a title plus a smaller explanatory second line.

        Kept here so a screen that retitles a button later (the lobby's skin
        picker) rebuilds it exactly the same way.
        """
        if not subtitle:
            return text
        # FONT_SMALL, not the old FONT_TINY: a subtitle is a sentence ("Friends
        # join from your Wi-Fi") and 12sp is under Android's legibility floor.
        return f"{text}\n[size={int(theme.FONT_SMALL)}]{subtitle}[/size]"

    def _sync_text_size(self, *_) -> None:
        # Without an explicit text_size, halign does nothing and long labels
        # run off the edge instead of wrapping.
        self.text_size = (max(0.0, self.width - 2 * theme.SPACE_3), None)

    def _sync_style(self, *_) -> None:
        if self.disabled:
            self._set_fill(theme.DISABLED, (0, 0, 0, 0))
            self.color = theme.DISABLED_TEXT
            return
        self.color = self._c_text
        self._set_fill(self._c_pressed if self.state == "down"
                       else self._c_normal, self._c_outline)


class MenuButton(TouchButton):
    """Full-width button for menus and lobbies."""


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


class Title(Label):
    def __init__(self, text: str, size: float = theme.FONT_TITLE, **kw) -> None:
        kw.setdefault("color", theme.FG)
        super().__init__(
            text=text,
            font_size=size,
            size_hint_y=None,
            height=size * 1.5,
            **kw,
        )


class Caption(Label):
    """Small grey line: taglines, hints, status, the word above a value."""

    def __init__(self, text: str = "", size: float = theme.FONT_SMALL,
                 color=None, **kw) -> None:
        kw.setdefault("halign", "center")
        kw.setdefault("valign", "middle")
        super().__init__(
            text=text,
            font_size=size,
            color=color or theme.GROUND,
            size_hint_y=None,
            height=size * 1.7,
            **kw,
        )
        self.bind(size=lambda *_: setattr(self, "text_size",
                                          (self.width, None)))


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------


class Card(BoxLayout, _RoundedBackground):
    """A rounded surface that groups related things.

    Vertical cards size themselves to their contents, because the menu column
    is content-sized: a card with the default size_hint would collapse to
    nothing there.
    """

    def __init__(self, orientation: str = "vertical", fill=None, outline=None,
                 radius=None, auto_height: bool | None = None, **kw) -> None:
        kw.setdefault("padding", theme.CARD_PAD)
        kw.setdefault("spacing", theme.GAP_SM)
        super().__init__(orientation=orientation, **kw)
        self._init_background(fill or theme.SURFACE,
                              theme.BORDER if outline is None else outline,
                              radius)
        if auto_height is None:
            auto_height = orientation == "vertical"
        if auto_height:
            self.size_hint_y = None
            self.bind(minimum_height=self.setter("height"))


class Divider(Widget):
    def __init__(self, **kw) -> None:
        super().__init__(size_hint_y=None, height=theme.BORDER_WIDTH, **kw)
        with self.canvas:
            Color(*theme.BORDER)
            self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_) -> None:
        self._rect.pos = self.pos
        self._rect.size = self.size


class Badge(Label, _RoundedBackground):
    """A small pill: HOST, YOU, READY, an active power-up.

    Pills instead of a run-on string ("Rex - host, you - READY") because the
    eye finds a shape far faster than it parses punctuation.
    """

    def __init__(self, text: str, color=None, filled: bool = False, **kw) -> None:
        color = color or theme.GROUND
        super().__init__(
            text=text,
            font_size=theme.FONT_CAPTION,
            color=theme.ON_FILL if filled else color,
            size_hint=(None, None),
            # Derived from the type, not a fixed 20dp: sp() grows with the
            # user's system font-size setting, and a hard height clipped the
            # descenders off "READY" on anything above 100%.
            height=max(dp(22), theme.FONT_CAPTION * 1.9),
            **kw,
        )
        self._init_background((*color[:3], 1.0) if filled else (0, 0, 0, 0),
                              (0, 0, 0, 0) if filled else (*color[:3], 0.45),
                              radius=self.height * 0.5)
        self.bind(texture_size=self._sync_width)
        self._sync_width()

    def _sync_width(self, *_) -> None:
        self.width = self.texture_size[0] + 2 * theme.SPACE_2


class Dot(Widget):
    """A filled circle -- the player's dino colour, at roster scale."""

    def __init__(self, color=theme.ACCENT, diameter: float = dp(14), **kw) -> None:
        super().__init__(size_hint=(None, None), size=(diameter, diameter), **kw)
        with self.canvas:
            self._color = Color(*color)
            self._shape = RoundedRectangle(radius=[diameter * 0.5])
        self.bind(pos=self._sync, size=self._sync)
        self._sync()

    def set_color(self, color) -> None:
        self._color.rgba = color

    def _sync(self, *_) -> None:
        self._shape.pos = self.pos
        self._shape.size = self.size
        self._shape.radius = [min(self.size) * 0.5]


class Stat(BoxLayout):
    """A caption over a value: the unit of the game-over card and the HUD.

    The caption is what makes a bare number mean something. "1,240" alone is
    the reason the old HUD had to spell out "px".
    """

    def __init__(self, caption: str, value: str = "-",
                 value_size: float = theme.FONT_HEADING,
                 value_color=None, halign: str = "center", **kw) -> None:
        kw.setdefault("spacing", 0)
        super().__init__(orientation="vertical", **kw)
        self.caption_label = Label(
            text=caption.upper(), font_size=theme.FONT_CAPTION,
            color=theme.FAINT, size_hint_y=None, height=theme.FONT_CAPTION * 1.6,
            halign=halign, valign="bottom")
        self.value_label = Label(
            text=value, font_size=value_size, color=value_color or theme.FG,
            size_hint_y=None, height=value_size * 1.35,
            halign=halign, valign="top")
        for label in (self.caption_label, self.value_label):
            label.bind(size=lambda w, *_: setattr(w, "text_size",
                                                  (w.width, w.height)))
            self.add_widget(label)
        self.size_hint_y = None
        self.height = self.caption_label.height + self.value_label.height

    def set_value(self, text: str) -> None:
        self.value_label.text = text


class Meter(Widget):
    """A horizontal bar that fills 0..1. Used for the rope-tension readout.

    The fill changes colour as it rises -- green while there is slack, amber
    when the rope is taking up, red just before it yanks. A single-colour bar
    made you read the length to know whether you were in trouble; the colour
    is legible out of the corner of your eye, which is all the attention a
    runner leaves you.
    """

    def __init__(self, empty_color=(0.82, 0.82, 0.82, 1.0),
                 full_color=None, **kw) -> None:
        super().__init__(**kw)
        self.empty_color = empty_color
        self.full_color = full_color  # None = colour by value
        self._value = 0.0
        self.bind(pos=lambda *_: self._redraw(), size=lambda *_: self._redraw())

    def set_value(self, value: float) -> None:
        value = max(0.0, min(1.0, value))
        if abs(value - self._value) < 0.005:
            return
        self._value = value
        self._redraw()

    def _color(self):
        if self.full_color is not None:
            return self.full_color
        if self._value >= 0.8:
            return theme.DANGER
        if self._value >= 0.5:
            return theme.WARN
        return theme.ACCENT

    def _redraw(self) -> None:
        radius = min(self.height * 0.5, theme.RADIUS_SM)
        self.canvas.clear()
        with self.canvas:
            Color(*self.empty_color)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])
            if self._value <= 0.0:
                return
            Color(*self._color())
            # Never narrower than the cap radius, or the rounded fill inverts
            # into a sliver at low values.
            width = max(self.width * self._value, radius * 2.0)
            RoundedRectangle(pos=self.pos, size=(width, self.height),
                             radius=[radius])


# ---------------------------------------------------------------------------
# Screen scaffold
# ---------------------------------------------------------------------------


class Panel(FloatLayout):
    """Screen scaffold: painted background plus a centred content column.

    Menus are laid out in a column capped at CONTENT_MAX_WIDTH and centred,
    rather than filling the screen. On a phone held in landscape -- which is
    what this game runs in -- a full-bleed button would stretch to well over
    2000px, which looks broken and puts the label miles from your thumb.

    The column lives inside the SAFE area, not the window: the background is
    painted edge to edge (a black bar beside a notch looks broken) but nothing
    you have to read or hit goes under the camera or the gesture bar. Centring
    is against the safe box too, so a cutout down the left of a landscape phone
    shifts the column right rather than leaving it visually off-centre.

    ``add_widget`` forwards into that column, so screens using this class need
    no changes.
    """

    def __init__(self, **kw) -> None:
        self.column = None
        super().__init__(**kw)
        with self.canvas.before:
            Color(*theme.BG)
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_bg, size=self._sync_bg)

        # The column scrolls. A phone in landscape is only ~360dp tall, and a
        # title plus five 56dp buttons does not fit -- without this the top of
        # the screen is simply cut off, which is exactly how it looked first
        # time round. Positioned by hand rather than by pos_hint because the
        # frame it is centred in is the safe box, not the window.
        self.scroll = ScrollView(size_hint=(None, None),
                                 bar_width=dp(3), do_scroll_x=False)
        self.column = BoxLayout(
            orientation="vertical",
            spacing=theme.STACK_GAP,
            padding=(0, theme.SPACE_3, 0, theme.SPACE_3),
            size_hint_y=None,
        )
        self.column.bind(minimum_height=self._sync_height)
        self.scroll.add_widget(self.column)
        super().add_widget(self.scroll)

        # Bound to the viewport rather than to the Panel: the ScrollView's own
        # height is what the centring maths needs, and it lags the Panel's by
        # a layout pass.
        self.scroll.bind(height=self._sync_height)
        self.bind(pos=self._sync_column, size=self._sync_column)
        insets.bind_layout(self._sync_column)
        self._sync_column()

    def _sync_bg(self, *_) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size

    def _sync_height(self, *_) -> None:
        """Centre the column vertically while it fits; scroll once it does not.

        A ScrollView pins short content to the top, which left every menu
        hugging the ceiling with a third of the screen empty underneath. The
        padding is recomputed from the children's own height rather than from
        ``minimum_height`` (which includes the padding) so this cannot chase
        its own tail.
        """
        content = sum(child.height for child in self.column.children)
        content += self.column.spacing * max(0, len(self.column.children) - 1)
        slack = self.scroll.height - content
        floor = theme.SPACE_3
        pad = floor if slack < 2 * floor else slack * 0.5
        self.column.padding = (0, pad, 0, pad)
        self.column.height = content + 2 * pad

    def _sync_column(self, *_) -> None:
        if self.column is None:
            return
        sx, sy, sw, sh = insets.box(self.width, self.height)
        # EDGE on top of the system inset: the inset clears the notch, this
        # clears the rounded corner and stops the column touching the glass.
        avail = max(theme.SPACE_4, sw - 2 * theme.EDGE)
        width = min(avail, theme.CONTENT_MAX_WIDTH)

        self.scroll.width = width
        self.scroll.height = max(0.0, sh)
        self.scroll.x = self.x + sx + (sw - width) * 0.5
        self.scroll.y = self.y + sy
        self.column.width = width

    def add_widget(self, widget, *args, **kwargs):
        # Before the column exists we are being built; afterwards everything
        # a screen adds belongs inside it.
        if self.column is None:
            return super().add_widget(widget, *args, **kwargs)
        return self.column.add_widget(widget, *args, **kwargs)

    def spacer(self, height: float = theme.GAP) -> None:
        self.add_widget(Widget(size_hint_y=None, height=height))


class Dialog(ModalView):
    """A light, rounded modal that looks like the rest of the game.

    Kivy's stock Popup is dark chrome with a coloured separator bar -- fine
    for a debug tool, jarring in a near-white game. This is the same card
    surface as everywhere else, dimming the screen behind it, with a close
    button big enough to hit with a thumb.

    ``add_widget`` forwards into the body, so callers just add content.
    """

    def __init__(self, title: str, dismiss_text: str = "Close", **kw) -> None:
        self.body = None
        super().__init__(
            size_hint=(1, 1),
            auto_dismiss=True,
            # Two separate layers in Kivy: `overlay_color` dims the screen
            # (its default 70% black blacked the game out entirely) and
            # `background_color` tints the modal's own bitmap, which we do not
            # want at all -- the card below is the surface.
            overlay_color=theme.SCRIM,
            background_color=(0, 0, 0, 0),
            **kw,
        )

        # Padded by the system insets so a dialog is centred in the part of
        # the screen you can actually see, and its Close button never lands
        # under the gesture bar.
        self._anchor = AnchorLayout(anchor_x="center", anchor_y="center")
        # auto_height off: a dialog is sized to the screen, not to its text.
        card = Card(spacing=theme.GAP, auto_height=False)

        header = Label(text=title, font_size=theme.FONT_HEADING, color=theme.FG,
                       size_hint_y=None, height=theme.FONT_HEADING * 1.6)
        card.add_widget(header)
        card.add_widget(Divider())

        self.body = BoxLayout(orientation="vertical", spacing=theme.GAP,
                              padding=(0, theme.GAP_SM))
        card.add_widget(self.body)
        # A rule under the body, so scrollable content that runs past the
        # bottom is visibly cut off by an edge rather than by the button.
        card.add_widget(Divider())
        card.add_widget(TouchButton(dismiss_text, self.dismiss,
                                    variant="secondary"))

        self._anchor.add_widget(card)
        super().add_widget(self._anchor)

        self._card = card
        self.bind(size=self._sync_card)
        insets.bind_layout(self._sync_card)
        self._sync_card()

    def _sync_card(self, *_) -> None:
        # Cap the card so a dialog does not stretch to 2000px on a landscape
        # phone, and leave the screen edges visible so "tap outside to close"
        # is discoverable.
        self._anchor.padding = [insets.left + theme.EDGE,
                                insets.top + theme.EDGE,
                                insets.right + theme.EDGE,
                                insets.bottom + theme.EDGE]
        _, _, safe_w, safe_h = insets.box(self.width, self.height)
        avail_w = max(theme.SPACE_4, safe_w - 2 * theme.EDGE)
        avail_h = max(theme.SPACE_4, safe_h - 2 * theme.EDGE)

        self._card.width = min(avail_w, theme.CONTENT_MAX_WIDTH + dp(80))
        self._card.size_hint_x = None
        # No dp(460) ceiling any more: that was tuned for one screen shape, and
        # on a short landscape phone it fought the inset padding for the same
        # pixels. The safe box is the only real limit.
        self._card.height = min(avail_h, dp(460))
        self._card.size_hint_y = None

    def add_widget(self, widget, *args, **kwargs):
        if self.body is None:
            return super().add_widget(widget, *args, **kwargs)
        return self.body.add_widget(widget, *args, **kwargs)


class ConfirmDialog(Dialog):
    """Ask before doing something the player cannot undo.

    Exists because Back mid-run used to end the run outright. On Android that
    button is a system gesture -- a swipe from the edge of the screen, which is
    also where your thumb rests holding the phone in landscape -- so it gets
    hit by accident, and a good run vanished with no way back.

    The dismiss button is the SAFE option and the one in the body is the
    destructive one, so tapping outside, hitting Back again, or panicking all
    resolve to "carry on".
    """

    def __init__(self, title: str, message: str, confirm_text: str,
                 on_confirm: Callable[[], None],
                 cancel_text: str = "Keep playing",
                 on_cancel: Callable[[], None] | None = None, **kw) -> None:
        super().__init__(title=title, dismiss_text=cancel_text, **kw)
        self._on_cancel = on_cancel
        self._confirmed = False

        text = Label(text=message, font_size=theme.FONT_SMALL,
                     color=theme.GROUND, halign="center", valign="middle")
        text.bind(size=lambda w, *_: setattr(w, "text_size",
                                             (w.width, w.height)))
        self.add_widget(text)

        def confirm() -> None:
            self._confirmed = True
            self.dismiss()
            on_confirm()

        self.add_widget(TouchButton(confirm_text, confirm, variant="danger"))

    def on_dismiss(self) -> None:
        # Fires for every route out, including tapping the scrim -- so resuming
        # a paused game belongs here rather than on the Cancel button alone.
        if not self._confirmed and self._on_cancel is not None:
            self._on_cancel()
