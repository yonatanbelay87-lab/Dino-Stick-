"""The widget kit: candy buttons, cards, stats, badges, dialogs, meters.

Everything is drawn on the canvas from ``theme`` -- no images, no atlases, no
kv files, nothing fetched -- so the whole shell is one coherent surface over
the shared sunset scene.

Screens are meant to compose these and never touch a colour directly. When
they do, the screens drift apart, which is exactly how the first pass ended up
with three different greys for "secondary text".

The signature component is CandyButton: a rounded top face floating on a solid
thickness band, which collapses when you press it and springs back when you
let go. It replaces every flat button in the shell. ``TouchButton`` and
``MenuButton`` are kept as names so no call site had to change.
"""

from __future__ import annotations

from typing import Callable

from kivy.animation import Animation
from kivy.graphics import (Color, Ellipse, Line, PopMatrix, PushMatrix,
                           Rectangle, Rotate, RoundedRectangle, Scale,
                           Triangle)
from kivy.metrics import dp
from kivy.properties import NumericProperty, StringProperty
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from game import constants as C

from . import theme
from .insets import insets
from .scene import SunsetScene


def shade(colour, factor: float):
    """Lighten (>1) or darken (<1) a colour, keeping its alpha."""
    return (min(1.0, colour[0] * factor),
            min(1.0, colour[1] * factor),
            min(1.0, colour[2] * factor),
            colour[3] if len(colour) > 3 else 1.0)


def animate(widget, duration: float, transition: str = "out_cubic", **targets):
    """Animate, unless the player has asked for less motion.

    One helper so "reduce motion" is a single check rather than a flag every
    call site has to remember. With it on, the widget still ENDS UP in the
    right state -- it just gets there instantly, so nothing depends on an
    animation having run.
    """
    if theme.REDUCE_MOTION or duration <= 0:
        for name, value in targets.items():
            setattr(widget, name, value)
        return None
    anim = Animation(duration=duration, t=transition, **targets)
    anim.start(widget)
    return anim


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
        x, y = theme.snap(self.x), theme.snap(self.y)
        w, h = theme.snap(self.width), theme.snap(self.height)
        radius = theme.snap(min(self._radius, min(w, h) * 0.5)
                            if min(w, h) > 0 else self._radius)
        self._fill_rect.pos = (x, y)
        self._fill_rect.size = (w, h)
        self._fill_rect.radius = [radius]
        # Inset by half the stroke so the outline sits inside the shape rather
        # than straddling the edge, which reads as a blurry double border.
        inset = theme.BORDER_WIDTH * 0.5
        self._outline_line.rounded_rectangle = (
            x + inset, y + inset,
            max(0.0, w - 2 * inset), max(0.0, h - 2 * inset), radius)

    def _set_fill(self, fill, outline=None) -> None:
        self._fill_color.rgba = fill
        if outline is not None:
            self._outline_color.rgba = outline


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------


class CandyButton(ButtonBehavior, FloatLayout):
    """A chunky arcade key: rounded face on a solid thickness band.

    Why not Kivy's Button? Because a Button centres its text on the WIDGET, and
    the whole idea here is that the text rides the FACE, which moves. Composing
    ButtonBehavior with a label we position ourselves is far less fighting than
    overriding Label's canvas, and it costs one property proxy.

    Press behaviour: the face drops by ``depth`` almost instantly (60ms -- any
    slower and it feels like lag rather than contact) and the band collapses
    behind it, so the key looks physically pushed in. Release springs it back
    with an overshoot. Both are interruptible: mash it and it keeps up.

    Callers pick a ``variant`` (an emphasis role, see theme.BUTTON_VARIANTS)
    instead of colours. ``color_normal``/``color_pressed`` still override the
    fill, because a couple of call sites genuinely are one-offs.
    """

    text = StringProperty("")
    font_size = NumericProperty(theme.FONT_BODY)
    lift = NumericProperty(0.0)

    def __init__(self, text: str, on_press_cb: Callable[[], None],
                 variant: str = "primary",
                 color_normal=None, color_pressed=None,
                 height: float | None = None,
                 subtitle: str = "", **kw) -> None:
        style = theme.BUTTON_VARIANTS.get(variant,
                                          theme.BUTTON_VARIANTS["primary"])
        self._variant = variant
        self._c_face = color_normal or style["fill"]
        self._c_deep = color_pressed or style["deep"]
        self._c_outline = style["outline"]
        self._c_text = style["text"]
        self._wants_glow = style["glow"]

        # A quiet control should not look like a slab, but it still has to
        # answer a tap, so it keeps a shallow travel.
        self.depth = (dp(3) if variant == "quiet"
                      else theme.CANDY_DEPTH)

        # A two-line button needs two lines' worth of room. At the 48dp small
        # height a title plus a subtitle already filled it, and a player with
        # Android's font size turned up lost the second line entirely -- so a
        # subtitle raises the floor rather than being squeezed in.
        floor = theme.BUTTON_HEIGHT if subtitle else theme.TOUCH_MIN

        super().__init__(
            size_hint_y=None,
            height=max(height or theme.BUTTON_HEIGHT, floor + self.depth),
            **kw)

        self.lift = self.depth
        self._build_canvas()

        self._label = Label(
            text=self.compose(text, subtitle),
            markup=True,
            font_size=self.font_size,
            font_name=theme.FONT_DISPLAY_NAME,
            color=self._c_text,
            halign="center", valign="middle",
            size_hint=(None, None))
        self.add_widget(self._label)

        self.text = self.compose(text, subtitle)
        self.bind(text=self._sync_text, font_size=self._sync_text,
                  pos=self._sync_layout, size=self._sync_layout,
                  lift=self._sync_layout, state=self._sync_style,
                  disabled=self._sync_style)
        self.fbind("on_release", lambda *_: on_press_cb())
        self._sync_layout()
        self._sync_style()

    # -- text ---------------------------------------------------------------

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
        return (f"{text}\n[size={int(theme.FONT_SMALL)}]"
                f"[color=#00000099]{subtitle}[/color][/size]")

    def _sync_text(self, *_) -> None:
        self._label.text = self.text
        self._label.font_size = self.font_size

    # -- geometry -----------------------------------------------------------

    def _build_canvas(self) -> None:
        with self.canvas.before:
            self._glow_color = Color(*theme.DINO[:3],
                                     0.28 if self._wants_glow else 0.0)
            self._glow_rect = RoundedRectangle()
            self._deep_color = Color(*self._c_deep)
            self._deep_rect = RoundedRectangle()
            self._face_color = Color(*self._c_face)
            self._face_rect = RoundedRectangle()
            self._outline_color = Color(*self._c_outline)
            self._outline_line = Line(width=theme.BORDER_WIDTH)

    def _sync_layout(self, *_) -> None:
        if self.width <= 0 or self.height <= 0:
            return
        x, w = theme.snap(self.x), theme.snap(self.width)
        base_y = theme.snap(self.y)
        face_h = theme.snap(max(1.0, self.height - self.depth))
        face_y = theme.snap(self.y + self.lift)
        radius = [theme.snap(min(theme.RADIUS, face_h * 0.5))]

        # The band runs from the footprint's floor up to the top of the face,
        # so pressing the face down collapses the band behind it rather than
        # leaving a slab poking out of the top.
        band_h = max(1.0, (face_y - base_y) + face_h)
        self._deep_rect.pos = (x, base_y)
        self._deep_rect.size = (w, band_h)
        self._deep_rect.radius = radius

        self._face_rect.pos = (x, face_y)
        self._face_rect.size = (w, face_h)
        self._face_rect.radius = radius

        spread = theme.SPACE_1
        self._glow_rect.pos = (x - spread, base_y - spread)
        self._glow_rect.size = (w + 2 * spread, band_h + 2 * spread)
        self._glow_rect.radius = [radius[0] + spread]

        inset = theme.BORDER_WIDTH * 0.5
        self._outline_line.rounded_rectangle = (
            x + inset, face_y + inset,
            max(0.0, w - 2 * inset), max(0.0, face_h - 2 * inset),
            radius[0])

        # The label rides the face.
        self._label.size = (max(0.0, w - 2 * theme.SPACE_3), face_h)
        self._label.text_size = self._label.size
        self._label.pos = (x + theme.SPACE_3, face_y)

    # -- state --------------------------------------------------------------

    def _sync_style(self, *_) -> None:
        if self.disabled:
            self._face_color.rgba = theme.DISABLED
            self._deep_color.rgba = theme.DISABLED_DEEP
            self._glow_color.a = 0.0
            self._label.color = theme.DISABLED_TEXT
            self.lift = self.depth * 0.4
            return

        self._face_color.rgba = self._c_face
        self._deep_color.rgba = self._c_deep
        self._outline_color.rgba = self._c_outline
        self._label.color = self._c_text
        self._glow_color.a = 0.28 if self._wants_glow else 0.0

    def on_press(self) -> None:
        Animation.cancel_all(self, "lift")
        animate(self, theme.T_PRESS, "out_quad", lift=0.0)

    def on_release(self) -> None:
        Animation.cancel_all(self, "lift")
        animate(self, theme.T_SPRING, theme.EASE_SPRING, lift=self.depth)


# Names the screens already use. Kept so the redesign swapped the look without
# touching a single call site.
TouchButton = CandyButton


class MenuButton(CandyButton):
    """Full-width candy button for menus and lobbies."""


class IconButton(CandyButton):
    """A square candy button holding one drawn glyph: < > + copy.

    Deliberately NOT emoji or a font glyph. Neither Fredoka nor Kivy's Roboto
    carries the arrows and symbols this needs, and a missing glyph renders as a
    tofu box -- which is exactly what the in-game Quit button had to avoid.
    Every icon here is drawn from lines and triangles.
    """

    def __init__(self, icon: str, on_press_cb, size: float | None = None,
                 variant: str = "secondary", **kw) -> None:
        side = size or theme.TOUCH_MIN
        super().__init__("", on_press_cb, variant=variant,
                         height=side + theme.CANDY_DEPTH,
                         size_hint=(None, None), **kw)
        self.width = side
        self._icon = icon
        with self.canvas.after:
            self._icon_color = Color(*self._c_text)
            # Two strokes, because "copy" is two overlapping sheets and a
            # single polyline cannot draw disjoint shapes -- the first attempt
            # collapsed into one empty box, which reads as a missing glyph.
            self._stroke_a = Line(width=dp(2.0), cap="round", joint="round")
            self._stroke_b = Line(width=dp(2.0), cap="round", joint="round")
        self.bind(pos=self._sync_icon, size=self._sync_icon,
                  lift=self._sync_icon)
        self._sync_icon()

    def _sync_icon(self, *_) -> None:
        face_h = theme.snap(max(1.0, self.height - self.depth))
        cx = theme.snap(self.center_x)
        cy = theme.snap(self.y + self.lift + face_h * 0.5)
        r = theme.snap(min(self.width, face_h) * 0.20)
        self._stroke_b.points = []

        if self._icon in ("<", ">"):
            sign = -1.0 if self._icon == "<" else 1.0
            self._stroke_a.points = [cx - sign * r * 0.5, cy + r,
                                     cx + sign * r * 0.5, cy,
                                     cx - sign * r * 0.5, cy - r]
        elif self._icon == "copy":
            # Two offset sheets, the universal "duplicate": a full square in
            # front and the corner of a second one peeking out behind it.
            side = r * 1.5
            off = r * 0.5
            x0, y0 = cx - side * 0.5 - off * 0.5, cy - side * 0.5 - off * 0.5
            self._stroke_a.points = [x0, y0, x0 + side, y0,
                                     x0 + side, y0 + side, x0, y0 + side,
                                     x0, y0]
            bx, by = x0 + off, y0 + off
            # Just the exposed L of the sheet behind, so the two do not read
            # as one thick-walled box.
            self._stroke_b.points = [bx + side - off, by + off,
                                     bx + side, by + off,
                                     bx + side, by + side,
                                     bx + off, by + side,
                                     bx + off, by + side - off]
        else:
            self._stroke_a.points = []


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


class Title(Label):
    """A heading with a hard drop shadow behind it.

    The shadow is a second Label offset down-right, not a blur: over a gradient
    sky a soft shadow just muddies, while a hard offset copy reads as arcade
    signage and costs one extra draw.
    """

    def __init__(self, text: str, size: float = theme.FONT_TITLE, **kw) -> None:
        kw.setdefault("color", theme.FG)
        kw.setdefault("halign", "center")
        kw.setdefault("valign", "middle")
        super().__init__(
            text=text,
            font_size=size,
            font_name=theme.FONT_DISPLAY_NAME,
            size_hint_y=None,
            height=theme.snap(size * 1.5),
            **kw,
        )
        offset = theme.snap(max(dp(2), size * 0.055))
        with self.canvas.before:
            self._shadow_color = Color(*theme.INK[:3], 0.55)
            self._shadow = Rectangle()
        self._offset = offset
        # texture_size, not just texture. Kivy REUSES the Texture object
        # when a label re-lays-out, so binding to `texture` alone never
        # fired: the shadow kept the geometry from before text_size was
        # applied, and with halign="left" that left it centred while the
        # real ink sat left. The heading rendered as a visible second copy
        # -- "LOBBYLOBBY".
        self.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, None)))
        self.bind(pos=self._sync_shadow, size=self._sync_shadow,
                  texture=self._sync_shadow, texture_size=self._sync_shadow,
                  text_size=self._sync_shadow)
        self._sync_shadow()

    def _sync_shadow(self, *_) -> None:
        if self.texture is None:
            self._shadow.size = (0, 0)
            return
        tw, th = self.texture_size
        self._shadow.texture = self.texture
        self._shadow.size = (tw, th)
        # The same formula Kivy's own <Label> rule uses for the real
        # texture, so the two can only ever differ by the offset.
        self._shadow.pos = (
            theme.snap(self.center_x - tw * 0.5 + self._offset),
            theme.snap(self.center_y - th * 0.5 - self._offset))


class GameTitle(Widget):
    """The DINO STICK wordmark: cream + green, one hard shadow, one unit.

    Two Labels rather than markup, because the two halves need different
    colours AND a shared shadow that sits behind both -- markup would put the
    shadow inside the coloured text.
    """

    def __init__(self, size: float = theme.FONT_LOGO, **kw) -> None:
        kw.setdefault("size_hint_y", None)
        super().__init__(**kw)
        self.height = size * 1.35
        self._offset = max(dp(3), size * 0.06)

        common = dict(font_name=theme.FONT_DISPLAY_NAME, font_size=size,
                      size_hint=(None, None), halign="center", valign="middle")
        self._shadow_a = Label(text="DINO", color=(*theme.INK[:3], 0.6), **common)
        self._shadow_b = Label(text="STICK", color=(*theme.INK[:3], 0.6), **common)
        self._dino = Label(text="DINO", color=theme.CREAM, **common)
        self._stick = Label(text="STICK", color=theme.DINO, **common)
        for label in (self._shadow_a, self._shadow_b, self._dino, self._stick):
            label.bind(texture_size=lambda w, s: setattr(w, "size", s))
            self.add_widget(label)

        self.bind(pos=self._sync, size=self._sync)
        for label in (self._dino, self._stick):
            label.bind(texture_size=self._sync)
        self._sync()

    def _sync(self, *_) -> None:
        gap = theme.SPACE_3
        total = self._dino.width + gap + self._stick.width
        left = self.center_x - total * 0.5
        cy = self.center_y

        self._dino.center = (left + self._dino.width * 0.5, cy)
        self._stick.center = (left + self._dino.width + gap
                              + self._stick.width * 0.5, cy)
        self._shadow_a.center = (self._dino.center_x + self._offset,
                                 cy - self._offset)
        self._shadow_b.center = (self._stick.center_x + self._offset,
                                 cy - self._offset)
        # Shadows first in z-order: Kivy draws children back to front in
        # reverse-add order, so re-adding is the only way to be certain.
        for label in (self._shadow_a, self._shadow_b):
            label.size = label.texture_size


class Caption(Label):
    """Small line: taglines, hints, status, the word above a value."""

    def __init__(self, text: str = "", size: float = theme.FONT_SMALL,
                 color=None, mono: bool = False, **kw) -> None:
        kw.setdefault("halign", "center")
        kw.setdefault("valign", "middle")
        kw.setdefault("font_name", theme.FONT_MONO_NAME if mono
                      else theme.FONT_BODY_NAME)
        super().__init__(
            text=text,
            font_size=size,
            color=color or theme.GROUND,
            size_hint_y=None,
            height=theme.snap(size * 1.7),
            **kw,
        )
        self._min_height = theme.snap(size * 1.7)
        self.bind(width=lambda *_: setattr(self, "text_size",
                                           (self.width, None)),
                  texture_size=self._grow)

    def _grow(self, _widget, texture_size) -> None:
        # Grow to fit. A fixed one-line height silently clipped the second line
        # off every two-line caption ("Co-op endless runner / One rope."), and
        # a caption that has been cut in half is worse than no caption.
        self.height = theme.snap(max(self._min_height, texture_size[1]))


class Chip(ButtonBehavior, Label):
    """A tappable pill: "playing as Rex - tap to change".

    A pill rather than a button because it is a STATUS you may edit, not an
    action -- and giving it a candy slab would have put a third emphasis level
    next to Play, which is one too many.
    """

    def __init__(self, text: str = "", on_press_cb=None, **kw) -> None:
        kw.setdefault("halign", "center")
        kw.setdefault("valign", "middle")
        super().__init__(
            text=text,
            font_size=theme.FONT_SMALL,
            font_name=theme.FONT_BODY_NAME,
            color=theme.FG,
            size_hint_y=None,
            height=theme.snap(max(theme.TOUCH_MIN, theme.FONT_SMALL * 2.6)),
            **kw,
        )
        # Hug the text rather than filling the column.
        self.bind(texture_size=lambda _w, s: setattr(
            self, "width", theme.snap(s[0] + 2 * theme.SPACE_5)))
        with self.canvas.before:
            self._fill = Color(*theme.SURFACE)
            self._rect = RoundedRectangle()
            self._stroke = Color(*theme.BORDER)
            self._line = Line(width=theme.BORDER_WIDTH)
        self.bind(pos=self._sync, size=self._sync, state=self._sync_state)
        if on_press_cb is not None:
            self.fbind("on_release", lambda *_: on_press_cb())
        self._sync()

    def _sync(self, *_) -> None:
        x, y = theme.snap(self.x), theme.snap(self.y)
        w, h = theme.snap(self.width), theme.snap(self.height)
        radius = h * 0.5
        self._rect.pos = (x, y)
        self._rect.size = (w, h)
        self._rect.radius = [radius]
        self._line.rounded_rectangle = (x, y, w, h, radius)

    def _sync_state(self, *_) -> None:
        self._fill.rgba = (theme.SURFACE_ALT if self.state == "down"
                           else theme.SURFACE)


class Pop(FloatLayout):
    """A container that can be scaled and spun about its own centre.

    Kivy widgets have no transform of their own, so "bounce this in" normally
    turns into animating a font size -- which re-renders the glyph texture every
    frame and is genuinely expensive for a 52sp wordmark. A matrix around the
    canvas is free by comparison: the texture is rendered once and the GPU does
    the rest.

    Animate ``scale`` for a pop and ``spin`` for a flip. Both are plain Kivy
    properties, so ``Animation`` drives them directly.
    """

    scale = NumericProperty(1.0)
    spin = NumericProperty(0.0)  # degrees, anticlockwise

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        with self.canvas.before:
            PushMatrix()
            self._rotate = Rotate(angle=0.0, axis=(0, 0, 1))
            self._scale = Scale(1.0, 1.0, 1.0)
        with self.canvas.after:
            PopMatrix()
        self.bind(pos=self._sync_transform, size=self._sync_transform,
                  scale=self._sync_transform, spin=self._sync_transform)
        self._sync_transform()

    def _sync_transform(self, *_) -> None:
        centre = self.center
        self._rotate.origin = centre
        self._rotate.angle = self.spin
        self._scale.origin = centre
        self._scale.x = self._scale.y = self.scale


class TextLink(ButtonBehavior, Label):
    """A small underlined tappable line -- credits, version, "learn more".

    Visually tiny, physically 48dp. The whole point of a link is that it is
    quiet enough to ignore, which fights directly with Android's touch-target
    floor; the answer is a small glyph inside a big invisible hit area rather
    than a bigger word.

    Underlined rather than merely coloured: over a gradient sky a colour shift
    alone does not read as "tappable", and the shell has no other blue-text
    convention to lean on.
    """

    def __init__(self, text: str = "", on_press_cb=None, **kw) -> None:
        kw.setdefault("halign", "center")
        kw.setdefault("valign", "middle")
        super().__init__(
            text=text,
            font_size=theme.FONT_SMALL,
            font_name=theme.FONT_BODY_NAME,
            color=theme.FAINT,
            size_hint_y=None,
            height=theme.TOUCH_MIN,
            **kw,
        )
        with self.canvas.after:
            self._rule_color = Color(*theme.FAINT)
            self._rule = Rectangle()
        self.bind(pos=self._sync, size=self._sync, texture_size=self._sync,
                  state=self._sync_state)
        if on_press_cb is not None:
            self.fbind("on_release", lambda *_: on_press_cb())
        self._sync()

    def _sync(self, *_) -> None:
        # text_size is deliberately NOT set. Setting it makes texture_size
        # report the padded BOX rather than the glyph extent, so the underline
        # stretched the full width of the column instead of sitting under the
        # words. Left unset, the Label centres its texture on the widget and
        # texture_size is the real ink width.
        width = min(self.texture_size[0], self.width)
        height = self.texture_size[1]
        # Just under the baseline of the centred texture, not under the widget
        # -- the widget is mostly empty space for the thumb.
        self._rule.size = (width, max(1.0, dp(1)))
        self._rule.pos = (int(self.center_x - width * 0.5),
                          int(self.center_y - height * 0.5 - dp(2)))

    def _sync_state(self, *_) -> None:
        pressed = self.state == "down"
        self.color = theme.FG if pressed else theme.FAINT
        self._rule_color.rgba = theme.FG if pressed else theme.FAINT


def hug(widget, height: float | None = None) -> AnchorLayout:
    """Centre a content-sized widget in a full-width row.

    A pill stretched to the whole column reads as an empty input field rather
    than a chip. Kivy has no "shrink to fit and centre" in a vertical
    BoxLayout, so this is the wrapper that does it.
    """
    row = AnchorLayout(anchor_x="center", anchor_y="center",
                       size_hint_y=None,
                       height=height or getattr(widget, "height", theme.TOUCH_MIN))
    widget.size_hint_x = None
    row.add_widget(widget)
    widget.bind(height=lambda _w, value: setattr(row, "height", value))
    return row


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------


class Card(BoxLayout, _RoundedBackground):
    """A rounded surface that groups related things.

    Vertical cards size themselves to their contents, because the shell columns
    are content-sized: a card with the default size_hint would collapse to
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


class DashedCard(BoxLayout):
    """An empty slot: dashed outline, no fill. "waiting for player 2..."

    Dashes say "something belongs here and has not arrived" in a way a faint
    solid box does not -- a solid box just reads as a card that failed to load.
    """

    def __init__(self, **kw) -> None:
        kw.setdefault("padding", theme.CARD_PAD)
        kw.setdefault("spacing", theme.GAP_SM)
        kw.setdefault("orientation", "vertical")
        super().__init__(**kw)
        with self.canvas.before:
            Color(*theme.CREAM[:3], 0.22)
            self._line = Line(width=theme.BORDER_WIDTH, dash_length=dp(6),
                              dash_offset=dp(5))
        self.bind(pos=self._sync, size=self._sync)
        self._sync()

    def _sync(self, *_) -> None:
        self._line.rounded_rectangle = (
            theme.snap(self.x), theme.snap(self.y),
            theme.snap(self.width), theme.snap(self.height),
            theme.snap(theme.RADIUS))


class Divider(Widget):
    def __init__(self, **kw) -> None:
        super().__init__(size_hint_y=None,
                         height=max(1.0, theme.snap(theme.BORDER_WIDTH)),
                         **kw)
        with self.canvas:
            Color(*theme.BORDER)
            self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_) -> None:
        self._rect.pos = (theme.snap(self.x), theme.snap(self.y))
        self._rect.size = (theme.snap(self.width), theme.snap(self.height))


class Badge(Label):
    """A small pill: HOST, YOU, READY, an active power-up.

    Pills instead of a run-on string ("Rex - host, you - READY") because the
    eye finds a shape far faster than it parses punctuation.
    """

    def __init__(self, text: str, color=None, filled: bool = False, **kw) -> None:
        color = color or theme.GROUND
        self._filled = filled
        super().__init__(
            text=text,
            font_size=theme.FONT_CAPTION,
            font_name=theme.FONT_DISPLAY_NAME,
            color=theme.INK if filled else color,
            size_hint=(None, None),
            # Derived from the type, not a fixed 20dp: sp() grows with the
            # user's system font-size setting, and a hard height clipped the
            # descenders off "READY" on anything above 100%.
            height=theme.snap(max(dp(22), theme.FONT_CAPTION * 1.9)),
            **kw,
        )
        with self.canvas.before:
            self._fill = Color(*((*color[:3], 1.0) if filled else (0, 0, 0, 0)))
            self._rect = RoundedRectangle()
            self._stroke = Color(*((0, 0, 0, 0) if filled
                                   else (*color[:3], 0.5)))
            self._line = Line(width=theme.BORDER_WIDTH)
        self.bind(texture_size=self._sync, pos=self._sync, size=self._sync)
        self._sync()

    def _sync(self, *_) -> None:
        self.width = theme.snap(self.texture_size[0] + 2 * theme.SPACE_2)
        x, y = theme.snap(self.x), theme.snap(self.y)
        w, h = theme.snap(self.width), theme.snap(self.height)
        radius = h * 0.5
        self._rect.pos = (x, y)
        self._rect.size = (w, h)
        self._rect.radius = [radius]
        self._line.rounded_rectangle = (x, y, w, h, radius)


class LiveDot(Widget):
    """A small green dot with a slow heartbeat: the session is up.

    A static dot reads as an icon; a pulsing one reads as a signal. It is
    the only thing on the lobby that says "this is live and listening"
    while nothing else is moving.

    The pulse is opt-in via start_pulse() rather than automatic, because a
    looping Animation on a screen nobody is looking at is pure waste --
    the lobby starts it on enter and stops it on leave.
    """

    pulse = NumericProperty(0.0)  # 0 = resting, 1 = fully expanded

    def __init__(self, diameter: float | None = None, **kw) -> None:
        size = theme.snap(diameter or dp(10))
        super().__init__(size_hint=(None, None), size=(size, size), **kw)
        with self.canvas:
            self.halo_color = Color(*theme.DINO[:3], 0.35)
            self._halo = Ellipse()
            self.dot_color = Color(*theme.DINO)
            self._dot = Ellipse()
        self.bind(pos=self._sync, size=self._sync, pulse=self._sync)
        self._sync()

    def start_pulse(self) -> None:
        Animation.cancel_all(self, "pulse")
        if theme.REDUCE_MOTION:
            self.pulse = 0.35
            return
        beat = (Animation(pulse=1.0, d=0.75, t="out_sine")
                + Animation(pulse=0.0, d=0.75, t="in_sine"))
        beat.repeat = True
        beat.start(self)

    def stop_pulse(self) -> None:
        Animation.cancel_all(self, "pulse")
        self.pulse = 0.0

    def _sync(self, *_) -> None:
        x, y = theme.snap(self.x), theme.snap(self.y)
        w, h = theme.snap(self.width), theme.snap(self.height)
        spread = w * (0.55 + 0.85 * self.pulse)
        self.halo_color.a = 0.40 * (1.0 - self.pulse * 0.75)
        self._halo.pos = (x - spread * 0.5, y - spread * 0.5)
        self._halo.size = (w + spread, h + spread)
        self._dot.pos = (x, y)
        self._dot.size = (w, h)


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


class DinoAvatar(Widget):
    """A real dino, drawn from the game's own SKINS data.

    Not an emoji and not a sprite file. The game already describes every
    character as normalised shapes in ``constants.SKINS`` -- the same data the
    renderer draws the running dino from -- so the lobby avatar cannot drift
    out of sync with what you actually play as, and it ships as zero bytes of
    assets. Read-only use of that table; nothing in the game is touched.
    """

    def __init__(self, skin: int = 0, framed: bool = True, **kw) -> None:
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(44), dp(44)))
        super().__init__(**kw)
        self._skin = skin
        # The frame is not decoration, it is legibility. Rex is #525252 and the
        # cards are near-black: unframed, the default dino was a dark smudge on
        # a dark panel at about 1.1:1. The game itself runs these silhouettes
        # against a near-white sky, so the portrait tile gives them the
        # background they were drawn for.
        self._framed = framed
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def set_skin(self, skin: int) -> None:
        if skin == self._skin:
            return
        self._skin = skin
        self._redraw()

    def _redraw(self, *_) -> None:
        self.canvas.clear()
        if self.width <= 0 or self.height <= 0:
            return
        spec = C.SKINS[self._skin % len(C.SKINS)]
        base = spec["color"]

        # The character box is taller than it is wide, so fit it into the
        # widget by height and centre it -- squashing a dino to fill a square
        # is the one distortion the eye reads instantly.
        pad = self.height * 0.12 if self._framed else 0.0
        aspect = C.PLAYER_WIDTH / C.PLAYER_HEIGHT
        h = self.height - 2 * pad
        w = h * aspect
        ox = self.x + (self.width - w) * 0.5
        oy = self.y + pad

        with self.canvas:
            if self._framed:
                # The tile is the one part of this widget with a hard
                # edge, so it gets snapped; the silhouette inside does
                # not, because a half pixel is invisible on a shape with
                # no outline.
                Color(*theme.CREAM[:3], 0.93)
                fx, fy = theme.snap(self.x), theme.snap(self.y)
                fw, fh = theme.snap(self.width), theme.snap(self.height)
                RoundedRectangle(pos=(fx, fy), size=(fw, fh),
                                 radius=[theme.snap(min(fw, fh) * 0.28)])
            for part in spec["parts"]:
                Color(*shade(base, part.get("s", 1.0)))
                kind = part["k"]
                if kind == "rect":
                    x, y, pw, ph = part["r"]
                    Rectangle(pos=(ox + x * w, oy + y * h),
                              size=(pw * w, ph * h))
                elif kind == "ellipse":
                    x, y, pw, ph = part["r"]
                    Ellipse(pos=(ox + x * w, oy + y * h),
                            size=(pw * w, ph * h))
                elif kind == "tri":
                    p = part["p"]
                    Triangle(points=[ox + p[0] * w, oy + p[1] * h,
                                     ox + p[2] * w, oy + p[3] * h,
                                     ox + p[4] * w, oy + p[5] * h])
            eye_x, eye_y = spec["eye"]
            size = C.EYE_SIZE / C.PLAYER_HEIGHT * h
            Color(*C.COLOR_EYE)
            Ellipse(pos=(ox + eye_x * w - size * 0.5,
                         oy + eye_y * h - size * 0.5),
                    size=(size, size))


class DinoPicker(Card):
    """< [dino] Rex > -- pick your character without leaving the lobby.

    Arrow pickers rather than a grid: there are six dinos, they are purely
    cosmetic, and a grid would demand a whole screen for a choice nobody
    agonises over. The avatar is drawn live from the game's own skin data, so
    what you see here is exactly what runs.

    The arrows are separate 48dp targets from the card itself, so a thumb
    aiming for "next" cannot accidentally hit the card and vice versa.
    """

    def __init__(self, on_change: Callable[[int], None], skin: int = 0,
                 **kw) -> None:
        kw.setdefault("orientation", "horizontal")
        kw.setdefault("auto_height", False)
        kw.setdefault("size_hint_y", None)
        kw.setdefault("spacing", theme.SPACE_2)
        kw.setdefault("padding", theme.SPACE_2)
        super().__init__(**kw)
        # Was dp(88), which on a 360dp-tall phone is a quarter of the
        # column for a cosmetic choice. The arrows keep their 48dp targets
        # -- only the surrounding air shrinks.
        self.height = theme.snap(max(dp(74), theme.TOUCH_MIN
                                     + theme.CANDY_DEPTH
                                     + theme.FONT_SMALL * 1.6))
        self._on_change = on_change
        self._skin = skin

        # Quiet, not cream. Cream arrows carried the same visual weight as
        # START GAME sitting right underneath, so the eye had three equally
        # loud things to choose between on a screen with one real action.
        self.add_widget(IconButton("<", lambda: self._step(-1),
                                   variant="quiet",
                                   pos_hint={"center_y": 0.5}))

        middle = BoxLayout(orientation="vertical", spacing=0)
        self.avatar = DinoAvatar(skin, size=(dp(36), dp(36)),
                                 pos_hint={"center_x": 0.5})
        avatar_row = AnchorLayout(anchor_x="center", anchor_y="center")
        avatar_row.add_widget(self.avatar)
        middle.add_widget(avatar_row)
        self.name_label = Label(
            text="", font_size=theme.FONT_SMALL,
            font_name=theme.FONT_DISPLAY_NAME, color=theme.FG,
            size_hint_y=None,
            height=theme.snap(theme.FONT_SMALL * 1.6),
            halign="center", valign="middle")
        self.name_label.bind(size=lambda w, *_: setattr(
            w, "text_size", (w.width, w.height)))
        middle.add_widget(self.name_label)
        self.add_widget(middle)

        self.add_widget(IconButton(">", lambda: self._step(1),
                                   variant="quiet",
                                   pos_hint={"center_y": 0.5}))
        self.set_skin(skin)

    def _step(self, delta: int) -> None:
        self._on_change((self._skin + delta) % len(C.SKIN_NAMES))

    def set_skin(self, skin: int) -> None:
        self._skin = skin % len(C.SKIN_NAMES)
        self.avatar.set_skin(self._skin)
        self.name_label.text = C.SKIN_NAMES[self._skin]


class Stat(BoxLayout):
    """A caption over a value: the unit of the game-over card and the HUD.

    The caption is what makes a bare number mean something. "1,240" alone is
    the reason the old HUD had to spell out "px".
    """

    def __init__(self, caption: str, value: str = "-",
                 value_size: float = theme.FONT_HEADING,
                 value_color=None, halign: str = "center",
                 caption_color=None, **kw) -> None:
        kw.setdefault("spacing", 0)
        super().__init__(orientation="vertical", **kw)
        self.caption_label = Label(
            text=caption.upper(), font_size=theme.FONT_CAPTION,
            font_name=theme.FONT_BODY_NAME,
            color=caption_color or theme.FAINT,
            size_hint_y=None,
            height=theme.snap(theme.FONT_CAPTION * 1.6),
            halign=halign, valign="bottom")
        self.value_label = Label(
            text=value, font_size=value_size, color=value_color or theme.FG,
            font_name=theme.FONT_DISPLAY_NAME,
            size_hint_y=None, height=theme.snap(value_size * 1.35),
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

    def __init__(self, empty_color=None, full_color=None, **kw) -> None:
        super().__init__(**kw)
        self.empty_color = empty_color or (*theme.INK[:3], 0.45)
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


class _Column(ScrollView):
    """A vertical stack that centres itself and does NOT scroll if it fits.

    Landscape phones are short. The shell is laid out in two columns precisely
    so it never has to scroll -- but "never" cannot be a promise when the
    player has Android's font size at 130% and a four-player roster, so this
    keeps a safety valve: scrolling is switched ON only when the content
    genuinely does not fit, and OFF the rest of the time.

    Without the valve, an overflowing column would simply be cut off with no
    way to reach the button at the bottom, which is a dead end.
    """

    content_height = NumericProperty(0.0)
    # Blank space above the content. The Panel sets this on BOTH columns at
    # once so a two-column screen shares one shoulder line -- see Panel._balance.
    lead = NumericProperty(0.0)

    def __init__(self, align: str = "center", **kw) -> None:
        super().__init__(size_hint=(None, None), bar_width=theme.SCROLLBAR_WIDTH,
                         do_scroll_x=False, do_scroll_y=False,
                         effect_cls="ScrollEffect", **kw)
        # "center" suits a column whose content is a balanced block. "top"
        # suits a pair of columns holding DIFFERENT amounts -- centring those
        # independently starts them at two unrelated heights, and the two
        # halves stop reading as one screen.
        self.align = align
        # Assigned only AFTER the viewport has it, because add_widget below
        # forwards into self.box and would otherwise be handed the box itself.
        self.box = None
        self._fitting = False
        box = BoxLayout(orientation="vertical", spacing=theme.STACK_GAP,
                        size_hint_y=None)
        box.bind(minimum_height=self._fit)
        super().add_widget(box)
        self.box = box
        self.bind(height=self._fit, width=self._sync_width, lead=self._fit)

    def _sync_width(self, *_) -> None:
        self.box.width = self.width

    def _fit(self, *_) -> None:
        """Centre while it fits; tighten, then scroll, once it does not."""
        if self._fitting:
            return
        self._fitting = True
        try:
            self._fit_now()
        finally:
            self._fitting = False

    def _fit_now(self) -> None:
        # Always start from the full gap. Without this the tighten pass below
        # is a one-way latch: a four-player roster squeezes the spacing, and
        # when three of them leave the column keeps the cramped gaps forever.
        # Re-entrancy is guarded because writing `spacing` fires
        # `minimum_height`, which lands straight back here.
        self.box.spacing = theme.STACK_GAP
        children = [c for c in self.box.children if c.height > 0]
        content = sum(c.height for c in children)
        content += self.box.spacing * max(0, len(children) - 1)
        self.content_height = content

        slack = self.height - content
        if slack >= 0:
            self.do_scroll_y = False
            if self.align == "top":
                # A vertical BoxLayout packs from the BOTTOM upwards, so any
                # slack inside the box lands above the content -- sizing the
                # box to the full viewport bottom-aligns it, which is the
                # opposite of what "top" means. Size the box to its content
                # plus the shared lead, and let the ScrollView pin that to the
                # top, which is what a ScrollView does with content shorter
                # than its viewport.
                lead = min(self.lead, slack)
                self.box.padding = (0, lead, 0, 0)
                self.box.height = content + lead
            else:
                pad = slack * 0.5
                self.box.padding = (0, pad, 0, pad)
                self.box.height = self.height
            return

        # Overflowing. Give back the inter-item spacing first -- losing a few
        # dp of air is far less bad than losing a button off the bottom.
        tight = max(theme.SPACE_1, theme.STACK_GAP + slack /
                    max(1, len(children) - 1))
        self.box.spacing = tight
        content = sum(c.height for c in children)
        content += tight * max(0, len(children) - 1)

        self.box.padding = (0, 0, 0, 0)
        self.box.height = content
        self.do_scroll_y = content > self.height

    def add_widget(self, widget, *args, **kwargs):
        if self.box is None:
            return super().add_widget(widget, *args, **kwargs)
        widget.bind(height=self._fit, opacity=self._fit)
        return self.box.add_widget(widget, *args, **kwargs)


class Panel(FloatLayout):
    """Screen scaffold: the shared sunset scene plus one or two safe columns.

    Landscape, full screen, no scrolling. A phone held sideways gives roughly
    360dp of HEIGHT, which a title plus four 58dp buttons does not fit into --
    so the shell screens put content SIDE BY SIDE rather than stacking it and
    hoping. ``Panel(columns=2)`` gives you ``.left`` and ``.right``.

    Both columns live inside the SAFE area: the scene is painted edge to edge
    (a black bar beside a notch looks broken) but nothing you have to read or
    hit goes under the camera or the gesture bar. Centring is against the safe
    box too, so a cutout down the left of a landscape phone shifts the content
    right rather than leaving it visually off-centre.

    ``add_widget`` forwards into the left/only column, so single-column screens
    written against the old Panel need no changes.
    """

    def __init__(self, columns: int = 1, align: str = "center", **kw) -> None:
        self.col_left = None
        super().__init__(**kw)

        self.scene = SunsetScene(size_hint=(1, 1))
        super().add_widget(self.scene)

        self.columns = max(1, min(2, columns))
        # NOT `left`/`right`: Kivy's Widget already owns `right` as a
        # position alias, and assigning a column to it silently corrupts the
        # widget's own geometry.
        self.col_left = _Column(align=align)
        super().add_widget(self.col_left)
        self.col_right = None
        if self.columns == 2:
            self.col_right = _Column(align=align)
            super().add_widget(self.col_right)

        for column in self._columns():
            column.bind(content_height=self._balance)

        self.bind(pos=self._sync_layout, size=self._sync_layout)
        insets.bind_layout(self._sync_layout)
        self._sync_layout()

    def _columns(self) -> list:
        return [c for c in (self.col_left, self.col_right) if c is not None]

    def _balance(self, *_) -> None:
        """Give both columns ONE shoulder line, and centre the pair.

        Two columns holding different amounts is the normal case here -- four
        roster rows against three controls. Centring each independently starts
        them at two unrelated heights and the screen stops reading as one
        thing; top-aligning both dumps all the slack at the bottom and leaves
        half the screen empty. So: measure the TALLER column, centre that, and
        hang the shorter one from the same line.
        """
        columns = self._columns()
        if not columns or self.col_left.align != "top":
            return
        tallest = max(c.content_height for c in columns)
        lead = max(0.0, (self.col_left.height - tallest) * 0.5)
        for column in columns:
            column.lead = lead

    def _sync_layout(self, *_) -> None:
        if self.col_left is None:
            return
        self.scene.pos = self.pos
        self.scene.size = self.size

        sx, sy, sw, sh = insets.box(self.width, self.height)
        # EDGE on top of the system inset: the inset clears the notch, this
        # clears the rounded corner and stops content touching the glass.
        avail_w = max(theme.SPACE_4, sw - 2 * theme.EDGE)
        avail_h = max(theme.SPACE_4, sh - 2 * theme.EDGE)
        origin_y = self.y + sy + theme.EDGE

        # Snapped: a column is the frame every card inside it is measured
        # against, so half a pixel here drifts the two halves out of alignment
        # with each other even though each card rounds its own fill.
        avail_h = theme.snap(avail_h)
        if self.columns == 1:
            width = theme.snap(min(avail_w, theme.CONTENT_MAX_WIDTH))
            self.col_left.size = (width, avail_h)
            self.col_left.pos = (theme.snap(self.x + sx + (sw - width) * 0.5),
                                 theme.snap(origin_y))
            return

        total = min(avail_w, theme.SCENE_MAX_WIDTH)
        column = theme.snap((total - theme.COLUMN_GAP) * 0.5)
        start = theme.snap(self.x + sx + (sw - total) * 0.5)
        origin_y = theme.snap(origin_y)
        self.col_left.size = (column, avail_h)
        self.col_left.pos = (start, origin_y)
        self.col_right.size = (column, avail_h)
        self.col_right.pos = (start + column + theme.COLUMN_GAP, origin_y)
        self._balance()

    def add_widget(self, widget, *args, **kwargs):
        # Before the columns exist we are being built; afterwards everything a
        # screen adds belongs inside the left/only one.
        if self.col_left is None:
            return super().add_widget(widget, *args, **kwargs)
        return self.col_left.add_widget(widget, *args, **kwargs)

    def spacer(self, height: float = theme.GAP, column=None) -> None:
        (column or self.col_left).add_widget(
            Widget(size_hint_y=None, height=height))


class Dialog(ModalView):
    """A modal that looks like the rest of the game.

    Kivy's stock Popup is dark chrome with a coloured separator bar -- fine
    for a debug tool, jarring here. This is the same card surface as
    everywhere else, dimming the scene behind it, with a close button big
    enough to hit with a thumb.

    ``add_widget`` forwards into the body, so callers just add content.
    """

    def __init__(self, title: str, dismiss_text: str = "Close", **kw) -> None:
        self.body = None
        super().__init__(
            size_hint=(1, 1),
            auto_dismiss=True,
            # Two separate layers in Kivy: `overlay_color` dims the screen
            # (its default 70% black blacked the scene out entirely) and
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
        card = Card(spacing=theme.GAP, auto_height=False,
                    fill=theme.rgba(0x1C0E2E, 0.96))

        header = Title(title, theme.FONT_HEADING)
        card.add_widget(header)
        card.add_widget(Divider())

        self.body = BoxLayout(orientation="vertical", spacing=theme.GAP,
                              padding=(0, theme.GAP_SM))
        card.add_widget(self.body)
        # A rule under the body, so scrollable content that runs past the
        # bottom is visibly cut off by an edge rather than by the button.
        card.add_widget(Divider())
        card.add_widget(CandyButton(dismiss_text, self.dismiss,
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

        self._card.width = min(avail_w, theme.SCENE_MAX_WIDTH)
        self._card.size_hint_x = None
        # No fixed ceiling: that was tuned for one screen shape, and on a short
        # landscape phone it fought the inset padding for the same pixels. The
        # safe box is the only real limit.
        self._card.height = avail_h
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
                     font_name=theme.FONT_BODY_NAME,
                     color=theme.GROUND, halign="center", valign="middle")
        text.bind(size=lambda w, *_: setattr(w, "text_size",
                                             (w.width, w.height)))
        self.add_widget(text)

        def confirm() -> None:
            self._confirmed = True
            self.dismiss()
            on_confirm()

        self.add_widget(CandyButton(confirm_text, confirm, variant="danger"))

    def on_dismiss(self) -> None:
        # Fires for every route out, including tapping the scrim -- so resuming
        # a paused game belongs here rather than on the Cancel button alone.
        if not self._confirmed and self._on_cancel is not None:
            self._on_cancel()
