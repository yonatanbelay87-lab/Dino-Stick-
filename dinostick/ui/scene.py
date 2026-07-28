"""The sunset canyon -- one background widget, shared by every shell screen.

Built entirely from canvas instructions and two generated textures. No image
files, no network, nothing to package: the sky is 256 bytes of gradient and
the sun is a small radial bloom, both computed on first use and shared by
every screen.

Layers, back to front:

    sky        a vertical gradient, violet -> magenta -> hot orange
    sun        a soft radial bloom, its core tucked behind the near ridge
    far canyon a pale mesa silhouette
    near canyon a dark mesa silhouette, offset so the two never line up
    ground     a solid strip the screens' content sits above

The point of it being ONE widget is that the four shell screens cannot drift
apart. Add a layer here and it appears everywhere at once.

Cost: the sky is a single stretched texture, the sun is a single stretched
texture, and each canyon is one Mesh -- five draw calls for the whole scene.
It is rebuilt only when the widget resizes, never per frame, so an idle menu
costs nothing.
"""

from __future__ import annotations

from kivy.graphics import Color, Mesh, Rectangle
from kivy.graphics.texture import Texture
from kivy.uix.widget import Widget

from . import theme

# ---------------------------------------------------------------------------
# Sky
# ---------------------------------------------------------------------------

# Gradient stops as (position from the BOTTOM, colour). Bottom is the horizon,
# so the hot colour lives at 0.0.
#
# The warm band reaches up to 0.34, well ABOVE the tallest near-canyon mesa
# (0.38) and most of the far ridge. At the first tuning it stopped at 0.26 and
# the whole orange horizon was hidden behind the silhouettes -- the sky went
# violet to magenta and simply never got hot, which is the one colour the
# "sunset canyon" name is promising.
SKY_STOPS = (
    (0.00, theme.SKY_WARM),
    (0.34, theme.SKY_MID),
    (1.00, theme.SKY_TOP),
)

# Rows in the gradient texture. 256 is far more than the eye can resolve across
# a phone's short axis and still trivial to build.
SKY_STEPS = 256

_sky_texture: Texture | None = None


def _lerp(a, b, t: float):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def sky_texture() -> Texture:
    """A 1 x SKY_STEPS vertical gradient, built once and shared.

    One pixel wide: the GPU stretches it across the whole screen for free, and
    a wider texture would be storing the same column over and over.
    """
    global _sky_texture
    if _sky_texture is not None:
        return _sky_texture

    buf = bytearray()
    for row in range(SKY_STEPS):
        pos = row / (SKY_STEPS - 1)
        # Find the pair of stops this row falls between.
        lower = SKY_STOPS[0]
        upper = SKY_STOPS[-1]
        for index in range(len(SKY_STOPS) - 1):
            if SKY_STOPS[index][0] <= pos <= SKY_STOPS[index + 1][0]:
                lower, upper = SKY_STOPS[index], SKY_STOPS[index + 1]
                break
        span = max(1e-6, upper[0] - lower[0])
        colour = _lerp(lower[1][:3], upper[1][:3], (pos - lower[0]) / span)
        buf.extend(int(max(0.0, min(1.0, c)) * 255) for c in colour)

    texture = Texture.create(size=(1, SKY_STEPS), colorfmt="rgb")
    texture.blit_buffer(bytes(buf), colorfmt="rgb", bufferfmt="ubyte")
    # clamp, or the bottom row bleeds into the top one across the seam.
    texture.wrap = "clamp_to_edge"
    texture.mag_filter = "linear"
    texture.min_filter = "linear"
    _sky_texture = texture
    return texture


# ---------------------------------------------------------------------------
# Canyons
# ---------------------------------------------------------------------------
#
# A skyline as (x, height) control points, both normalised: x across the
# widget, height as a fraction of widget height measured up from the bottom.
# Authored rather than generated, because random noise gives you hills and this
# needs MESAS -- near-vertical risers into flat tops, which is what makes a
# desert read as a desert.
#
# The two profiles deliberately share no riser positions. When they did, the
# silhouettes lined up into one shape and the depth vanished.

CANYON_FAR = (
    (0.00, 0.30), (0.07, 0.30), (0.09, 0.47), (0.23, 0.47),
    (0.25, 0.34), (0.37, 0.34), (0.39, 0.53), (0.51, 0.53),
    (0.53, 0.36), (0.65, 0.36), (0.67, 0.49), (0.79, 0.49),
    (0.81, 0.32), (1.00, 0.32),
)

CANYON_NEAR = (
    (0.00, 0.19), (0.05, 0.19), (0.07, 0.32), (0.17, 0.32),
    (0.19, 0.21), (0.33, 0.21), (0.35, 0.38), (0.45, 0.38),
    (0.47, 0.23), (0.61, 0.23), (0.63, 0.34), (0.73, 0.34),
    (0.75, 0.20), (0.87, 0.20), (0.89, 0.29), (1.00, 0.29),
)

# Samples across the width. The profile is piecewise linear, so this only has
# to be dense enough that a near-vertical riser does not visibly stair-step.
CANYON_SAMPLES = 96

# Height of the solid strip at the very bottom, as a fraction of the widget.
GROUND_FRACTION = 0.10

# ---------------------------------------------------------------------------
# Sun
# ---------------------------------------------------------------------------

# Left of centre and low, so the DISC sits in the left column's airspace --
# behind a title, which is big shadowed type and can take it -- while the right
# column's buttons only ever cover the soft outer halo. Dead centre put a hard
# yellow sliver in the gap between two cream buttons, which read as a glitch
# rather than as a sun.
SUN_CENTRE = (0.38, 0.26)  # fraction of width, fraction of height
# The solid core sits BEHIND the near canyon on purpose. On a landscape phone
# the horizon is exactly where the content cards are, so any hard-edged bright
# disc eventually shows through a gap between two cards as a stray line. Tuck
# the core behind the ridge and let the soft halo be the visible sun: it still
# reads as a sunset -- arguably more so -- and there is no hard edge anywhere
# a card can slice through.
SUN_RADIUS = 0.11          # the solid disc, as a fraction of height
GLOW_SPREAD = 4.0          # halo extent, as a multiple of the disc radius
GLOW_ALPHA = 0.42          # alpha just outside the disc

# Resolution of the sun texture. The falloff is smooth in the alpha channel,
# so this only has to beat the eye, not the screen.
SUN_STEPS = 192

_sun_texture: Texture | None = None


def sun_texture() -> Texture:
    """Disc plus halo as ONE soft radial texture, built once and shared.

    The first attempt stacked five translucent ellipses. The result was five
    visible concentric BANDS -- a target, not a sunset -- because each ring has
    a hard edge and no amount of alpha tuning removes it. A texture puts the
    falloff in the alpha channel where it belongs and costs one draw call
    instead of six.
    """
    global _sun_texture
    if _sun_texture is not None:
        return _sun_texture

    core = 1.0 / GLOW_SPREAD  # where the solid disc ends, 0..1 of the radius
    # The disc does not end at a step. Going straight from alpha 1.0 to the
    # halo's 0.42 leaves a hard yellow rim, and anywhere a card gap crossed it
    # -- the 4dp between two roster rows -- that rim showed through as a stray
    # bright line that read as a rendering bug. Smoothstep across a feather
    # band instead.
    feather = core * 0.3
    red, green, blue = (int(c * 255) for c in theme.SUN[:3])
    buf = bytearray()
    half = (SUN_STEPS - 1) * 0.5
    for row in range(SUN_STEPS):
        for col in range(SUN_STEPS):
            dx = (col - half) / half
            dy = (row - half) / half
            dist = (dx * dx + dy * dy) ** 0.5
            if dist >= 1.0:
                alpha = 0.0
            elif dist <= core - feather:
                alpha = 1.0
            elif dist <= core:
                t = (dist - (core - feather)) / feather
                alpha = 1.0 + (GLOW_ALPHA - 1.0) * (t * t * (3.0 - 2.0 * t))
            else:
                # Squared falloff: linear leaves a visible rim where the halo
                # ends, the square eases into the sky.
                fade = 1.0 - (dist - core) / (1.0 - core)
                alpha = GLOW_ALPHA * fade * fade
            buf.extend((red, green, blue, int(alpha * 255)))

    texture = Texture.create(size=(SUN_STEPS, SUN_STEPS), colorfmt="rgba")
    texture.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    texture.wrap = "clamp_to_edge"
    texture.mag_filter = "linear"
    texture.min_filter = "linear"
    _sun_texture = texture
    return texture


def _profile_at(points, x: float) -> float:
    """Height of a skyline at normalised x, linearly interpolated."""
    if x <= points[0][0]:
        return points[0][1]
    for index in range(len(points) - 1):
        x0, y0 = points[index]
        x1, y1 = points[index + 1]
        if x0 <= x <= x1:
            if x1 - x0 < 1e-6:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


class SunsetScene(Widget):
    """The shared background. Add it first, fill the rest of the screen over it.

    ``sun_glow`` and ``sun_disc`` are exposed so the polish phase can pulse the
    halo without this class knowing anything about animation.
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.sun_glow: list[Color] = []
        self.sun_disc: Color | None = None
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    # -- drawing ------------------------------------------------------------

    def _redraw(self, *_args) -> None:
        self.canvas.clear()
        if self.width <= 0 or self.height <= 0:
            return

        self.sun_glow = []
        with self.canvas:
            self._draw_sky()
            self._draw_sun()
            self._draw_canyon(CANYON_FAR, theme.CANYON_BACK)
            self._draw_canyon(CANYON_NEAR, theme.CANYON)
            self._draw_ground()

    def _draw_sky(self) -> None:
        Color(1, 1, 1, 1)
        Rectangle(texture=sky_texture(), pos=self.pos, size=self.size)

    def _draw_sun(self) -> None:
        cx = self.x + self.width * SUN_CENTRE[0]
        cy = self.y + self.height * SUN_CENTRE[1]
        reach = self.height * SUN_RADIUS * GLOW_SPREAD

        # One texture carries the disc AND its halo, so the Color here is a
        # plain white multiplier. Exposed for the polish phase to pulse.
        self.sun_disc = Color(1, 1, 1, 1)
        self.sun_glow = [self.sun_disc]
        Rectangle(texture=sun_texture(),
                  pos=(cx - reach, cy - reach),
                  size=(reach * 2, reach * 2))

    def _draw_canyon(self, profile, colour) -> None:
        """One silhouette, as a triangle strip along the skyline.

        A strip rather than a fan or a polygon triangulator: a skyline is a
        heightfield, so two vertices per column -- one on the floor, one on the
        ridge -- describe it exactly and in order, which is precisely what a
        strip wants.
        """
        vertices: list[float] = []
        indices: list[int] = []
        base = self.y
        for step in range(CANYON_SAMPLES + 1):
            fraction = step / CANYON_SAMPLES
            x = self.x + self.width * fraction
            top = base + self.height * _profile_at(profile, fraction)
            # Kivy's default vertex format is x, y, u, v.
            vertices.extend((x, base, 0.0, 0.0))
            vertices.extend((x, top, 0.0, 0.0))
            indices.extend((step * 2, step * 2 + 1))

        Color(*colour)
        Mesh(vertices=vertices, indices=indices, mode="triangle_strip")

    def _draw_ground(self) -> None:
        height = self.height * GROUND_FRACTION
        Color(*theme.INK)
        Rectangle(pos=self.pos, size=(self.width, height))
        # A single warm line where the ground meets the canyon, so the strip
        # reads as lit ground rather than as a black bar.
        Color(*theme.SKY_WARM[:3], 0.35)
        Rectangle(pos=(self.x, self.y + height - theme.BORDER_WIDTH),
                  size=(self.width, theme.BORDER_WIDTH))
