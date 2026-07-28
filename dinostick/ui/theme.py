"""Palette, typography and metrics -- the whole design language in one file.

Everything is expressed in dp/sp rather than raw pixels. This is the single
most important change for Android: a 56-pixel button is comfortable in a
1280x720 desktop window and a fingernail-sized sliver on a 1080p phone, which
packs two to three times the pixels into the same physical inch. ``dp()``
scales with screen density so a 60dp control is roughly the same *physical*
size everywhere; ``sp()`` does the same for text.

On desktop dp() is 1:1, so the desktop build looks unchanged.

THE LOOK: "sunset canyon" -- an arcade dusk desert the dinos run into. Deep
violet overhead falling through magenta into a hot orange horizon, a low sun,
two layered canyon silhouettes, and one candy-green accent that means "go".

Two rules keep it coherent across the shell screens:

  * Colour carries meaning, never decoration. Green = go, cream = read this,
    sun = warning, red = you died. The sky is scenery and never carries state.
  * Exactly one primary (green candy) button per screen. If everything is
    emphasised, nothing is -- the first pass had four identical green bars on
    the menu and no way to tell which one you wanted.

Screens name ROLES (SURFACE, ACCENT, FG), never raw hex. The raw scene colours
below exist for the background painter and for nothing else.
"""

from __future__ import annotations

from kivy.metrics import dp, sp

from game import constants as C
from .fonts import BODY, DISPLAY, MONO

# ---------------------------------------------------------------------------
# Scene palette -- the literal art direction
# ---------------------------------------------------------------------------


def rgba(value: int, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """0xRRGGBB -> Kivy's 0..1 float tuple. Keeps the hex readable above."""
    return (((value >> 16) & 0xFF) / 255.0,
            ((value >> 8) & 0xFF) / 255.0,
            (value & 0xFF) / 255.0,
            alpha)


SKY_TOP = rgba(0x241145)      # violet, straight overhead
SKY_MID = rgba(0x7C2A6B)      # magenta band
SKY_WARM = rgba(0xE8663C)     # hot orange at the horizon
SUN = rgba(0xFFC24B)          # the low disc
DINO = rgba(0x2FD07F)         # the accent: go, ready, alive
DINO_DEEP = rgba(0x159A58)    # its shadow side / button thickness
CANYON = rgba(0x3A1236)       # near silhouette
CANYON_BACK = rgba(0x5A1F52)  # far silhouette
CREAM = rgba(0xFFF4E6)        # every word on screen
INK = rgba(0x1C0E2E)          # card bodies, text on bright fills

# Secondary candy fill: cream pressed into a warm tan so a cream button has a
# thickness band of its own. Sampled off CREAM rather than invented, so the two
# read as one material.
TAN = rgba(0xC9A882)
DANGER = rgba(0xE04A3F)       # crash red -- hotter than the sky, never in it
DANGER_DEEP = rgba(0xA8332B)

# ---------------------------------------------------------------------------
# Semantic roles
# ---------------------------------------------------------------------------
#
# Contrast was measured against the surfaces these actually sit on, not
# guessed. The old palette failed WCAG AA in three places (secondary text at
# 3.5:1, captions at 2.2:1, white-on-green at 3.3:1). This one clears AA
# everywhere and AAA for body text:
#
#   CREAM on SURFACE ................. 13.3:1
#   MUTED (cream 78%) on SURFACE ..... ~9:1
#   FAINT (cream 62%) on SURFACE ..... ~6:1
#   INK on DINO (primary button) ..... 9.1:1
#   INK on TAN (secondary button) .... 8.9:1
#   INK on DANGER .................... 4.5:1

BG = SKY_MID                  # only ever seen for one frame before the scene
SURFACE = rgba(0x1C0E2E, 0.82)      # a card floating on the sky
SURFACE_ALT = rgba(0x2E1A47, 0.88)  # inset rows, pressed states, fields
BORDER = rgba(0xFFF4E6, 0.14)       # hairline, just enough to define an edge

FG = CREAM
GROUND = rgba(0xFFF4E6, 0.78)  # secondary text
FAINT = rgba(0xFFF4E6, 0.62)   # captions, hints, disabled
ON_FILL = INK                  # text on a candy button: dark on bright

ACCENT = DINO
ACCENT_DEEP = DINO_DEEP
WARN = SUN

# Pressed states. Touch has no hover, so a press has to be visibly obvious or
# the control feels broken on a phone. The candy buttons also MOVE, which is
# the real feedback; these cover the flat controls.
ACCENT_PRESSED = DINO_DEEP
DANGER_PRESSED = DANGER_DEEP
MUTED = rgba(0xFFF4E6, 0.30)
MUTED_PRESSED = rgba(0xFFF4E6, 0.18)
# A disabled candy button is still a SLAB, just an inert one. The first pass
# made it cream at 14% -- over the bright canyon that washed out to almost
# nothing, and "Waiting for 2 players..." was the least readable text on the
# screen despite being the thing the host is waiting to read.
DISABLED = rgba(0x1C0E2E, 0.62)
DISABLED_DEEP = rgba(0x1C0E2E, 0.75)
DISABLED_TEXT = rgba(0xFFF4E6, 0.62)

# Dims the scene behind a dialog: enough to push it back, not so much that you
# lose your place. Kivy's own default here is 70% black, which reads as "the
# app closed".
SCRIM = rgba(0x1C0E2E, 0.62)

# ---------------------------------------------------------------------------
# In-game HUD
# ---------------------------------------------------------------------------
#
# The HUD is NOT a shell screen. It floats over the painted season backdrops,
# which are their own art and nothing to do with the sunset, so it keeps its
# own light card and dark text. These exist so the HUD stops borrowing FG /
# GROUND: those went cream with the redesign, and cream text on the HUD's white
# card would have been invisible.

HUD_SURFACE = (1.0, 1.0, 1.0, 0.88)
HUD_OUTLINE = (0.0, 0.0, 0.0, 0.08)
HUD_TEXT = (0.16, 0.14, 0.18, 1.0)
HUD_MUTED = (0.38, 0.36, 0.40, 1.0)
HUD_FAINT = (0.52, 0.50, 0.54, 1.0)

# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------
#
# "fill" is the top face, "deep" the solid thickness band under it, "text" what
# goes on top. Screens ask for a role ("primary", "quiet") rather than picking
# colours, so emphasis stays consistent from the menu through to the game-over
# card.

BUTTON_VARIANTS: dict[str, dict] = {
    # The one action the screen exists for. Gets the glow.
    "primary": {"fill": DINO, "deep": DINO_DEEP, "text": INK,
                "outline": (0, 0, 0, 0), "glow": True},
    # Real alternatives, equal weight to each other, lighter than primary.
    "secondary": {"fill": CREAM, "deep": TAN, "text": INK,
                  "outline": (0, 0, 0, 0), "glow": False},
    # Leaving, cancelling, help -- present but never competing for the eye.
    # No thickness band: a quiet control should not look like a slab.
    "quiet": {"fill": rgba(0xFFF4E6, 0.10), "deep": rgba(0xFFF4E6, 0.16),
              "text": CREAM, "outline": rgba(0xFFF4E6, 0.22), "glow": False},
    "danger": {"fill": DANGER, "deep": DANGER_DEEP, "text": INK,
               "outline": (0, 0, 0, 0), "glow": False},
    # Sits on top of the running game, over the season art rather than the
    # sunset, so it keeps the HUD's light material.
    "overlay": {"fill": (1.0, 1.0, 1.0, 0.90), "deep": (0.72, 0.70, 0.74, 0.92),
                "text": HUD_TEXT, "outline": (0, 0, 0, 0.10), "glow": False},
}

# ---------------------------------------------------------------------------
# Type
# ---------------------------------------------------------------------------
#
# Three roles, registered from local .ttf files in ui/fonts.py and falling back
# to Kivy's own Roboto if the files are not there yet.
#
#   FONT_DISPLAY_NAME  titles, buttons, numbers you should feel
#   FONT_BODY_NAME     everything you read
#   FONT_MONO_NAME     addresses and codes, where 0/O and 1/l must differ

FONT_DISPLAY_NAME = DISPLAY
FONT_BODY_NAME = BODY
FONT_MONO_NAME = MONO

# Sized against the real constraint: a phone in landscape gives roughly 360dp
# of HEIGHT. A title plus the buttons has to live inside that WITHOUT
# SCROLLING, which is why the shell screens are two columns.
#
# The floor is 14sp for anything that reads as a sentence. It used to be 11-12,
# which is where the HUD's "620 m - 32 km/h" and every button subtitle lived --
# small enough that a phone at arm's length in motion could not resolve them,
# and small enough to fail Android's own accessibility guidance.
#
# sp() also scales with the user's system font-size setting, so a player on
# "large text" gets 1.3x of all of these. Nothing may be sized to fit exactly.
FONT_LOGO = sp(52)  # the DINO STICK wordmark
FONT_DISPLAY = sp(44)  # the one number you actually want to see
FONT_TITLE = sp(34)
FONT_HEADING = sp(24)
FONT_BODY = sp(18)
FONT_SMALL = sp(14)  # the body floor: subtitles, hints, status lines
FONT_TINY = sp(13)  # nameplates over a dino's head; never a sentence
FONT_CAPTION = sp(12)  # the little uppercase word above a value

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
#
# One spacing scale, 4dp-based, and everything aligns to it. The previous set
# was 4/6/8/10/12 -- five values, three of them a couple of pixels apart, which
# is how the same "gap" ended up looking different on every screen.

SPACE_1 = dp(4)
SPACE_2 = dp(8)
SPACE_3 = dp(12)
SPACE_4 = dp(16)
SPACE_5 = dp(24)
SPACE_6 = dp(32)

PAD = SPACE_3
PAD_SM = SPACE_2
GAP = SPACE_2
GAP_SM = SPACE_1

# Vertical gap between stacked full-width buttons. Deliberately larger than
# GAP: 8dp between two 48dp targets is inside a thumb's own margin of error,
# and the miss you get is "Leave" when you meant "I'm Ready".
STACK_GAP = SPACE_3

# Distance any control keeps from a screen edge, on TOP of the system inset.
# The inset stops a control being under the notch; this stops it being welded
# to the glass, and covers the rounded corners no inset ever reports.
EDGE = SPACE_3

# Gap down the middle of a two-column shell screen.
COLUMN_GAP = SPACE_5

RADIUS = dp(16)
RADIUS_SM = dp(10)
RADIUS_PILL = dp(999)  # clamped to half the height by the drawing code
BORDER_WIDTH = dp(1.1)

# The solid band under a candy button's top face -- what makes it read as a
# physical key rather than a rectangle. It is also the distance the face
# travels on press, so it has to be big enough to SEE at arm's length and small
# enough not to eat the label's room.
CANDY_DEPTH = dp(6)
CANDY_DEPTH_SMALL = dp(4)

# Android's minimum recommended touch target is 48dp. Menu buttons sit above
# it -- they are hit with a thumb while gripping the phone in landscape -- but
# not so far above that four of them stop fitting on screen. The depth is part
# of the footprint, not on top of it, so the FACE stays comfortably tappable.
TOUCH_MIN = dp(48)
BUTTON_HEIGHT = dp(58)
BUTTON_HEIGHT_SMALL = dp(48)

# Menus are a centred column, not full-bleed: stretched across a 20:9 phone in
# landscape a single button becomes an absurd 2000px-wide bar.
CONTENT_MAX_WIDTH = dp(520)
# ...and a two-column screen gets the whole safe width, capped so a tablet does
# not push the two halves to opposite walls.
SCENE_MAX_WIDTH = dp(880)

ROW_HEIGHT = dp(48)  # a roster line, at the touch-target floor
CARD_PAD = SPACE_3

# ---------------------------------------------------------------------------
# Motion
# ---------------------------------------------------------------------------
#
# Short and interruptible. Anything above ~300ms on a control stops reading as
# feedback and starts reading as lag -- you have already looked away.

T_PRESS = 0.06   # face travelling down: as close to instant as is visible
T_SPRING = 0.20  # springing back up, overshooting slightly
T_ENTER = 0.28   # a title or card arriving
T_FADE = 0.18

EASE_SPRING = "out_back"
EASE_IN = "out_cubic"

# Set by ui.motion when the player turns motion down; every animation helper
# checks it. Kept here so a widget never has to import the settings store.
REDUCE_MOTION = False


# Kept for the renderer, which draws the game world and has its own palette in
# game/constants.py. Nothing in the shell should reach for these.
GAME_BG = C.COLOR_BG
