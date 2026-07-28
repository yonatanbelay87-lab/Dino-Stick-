"""Palette, typography and metrics -- the whole design language in one file.

Everything is expressed in dp/sp rather than raw pixels. This is the single
most important change for Android: a 56-pixel button is comfortable in a
1280x720 desktop window and a fingernail-sized sliver on a 1080p phone, which
packs two to three times the pixels into the same physical inch. ``dp()``
scales with screen density so a 60dp control is roughly the same *physical*
size everywhere; ``sp()`` does the same for text.

On desktop dp() is 1:1, so the desktop build looks unchanged.

The look is the game's own: flat, near-white, one green accent, and soft
rounded corners. Two rules keep it coherent across the four screens:

  * Colour carries meaning, never decoration. Green = go, red = you died or
    the rope is about to snap, amber = warning, grey = information.
  * Exactly one primary (filled green) button per screen. If everything is
    emphasised, nothing is -- the first pass had four identical green bars on
    the menu and no way to tell which one you wanted.
"""

from __future__ import annotations

from kivy.metrics import dp, sp

from game import constants as C

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

BG = C.COLOR_BG  # page background, matches the in-game sky
SURFACE = (1.0, 1.0, 1.0, 1.0)  # cards lifted off the background
SURFACE_ALT = (0.925, 0.925, 0.925, 1.0)  # pressed / inset rows
BORDER = (0.855, 0.855, 0.855, 1.0)  # hairline card outline

FG = C.COLOR_FG  # primary text
GROUND = C.COLOR_GROUND  # secondary text (same grey as the ground line)
FAINT = (0.66, 0.66, 0.66, 1.0)  # captions, hints, disabled text
ON_FILL = (1.0, 1.0, 1.0, 1.0)  # text on a filled button

ACCENT = C.COLOR_ACCENT
DANGER = C.COLOR_DANGER
WARN = (0.92, 0.62, 0.20, 1.0)  # matches the Sync power-up

# Pressed states. Touch has no hover, so a press has to be visibly obvious or
# the control feels broken on a phone.
ACCENT_PRESSED = (0.13, 0.45, 0.34, 1.0)
DANGER_PRESSED = (0.60, 0.18, 0.16, 1.0)
MUTED = (0.70, 0.70, 0.70, 1.0)
MUTED_PRESSED = (0.55, 0.55, 0.55, 1.0)
DISABLED = (0.86, 0.86, 0.86, 1.0)
DISABLED_TEXT = (0.62, 0.62, 0.62, 1.0)

# Dims the screen behind a dialog: enough to push it back, not so much that
# you lose your place. Kivy's own default here is 70% black, which reads as
# "the app closed".
SCRIM = (0.10, 0.10, 0.10, 0.38)

# Button variants: fill, pressed fill, text, outline. Screens ask for a role
# ("primary", "quiet") rather than picking colours, so emphasis stays
# consistent from the menu through to the game-over card.
BUTTON_VARIANTS: dict[str, dict] = {
    # The one action the screen exists for.
    "primary": {"fill": ACCENT, "down": ACCENT_PRESSED,
                "text": ON_FILL, "outline": (0, 0, 0, 0)},
    # Real alternatives, equal weight to each other, lighter than primary.
    "secondary": {"fill": SURFACE, "down": SURFACE_ALT,
                  "text": FG, "outline": BORDER},
    # Leaving, cancelling, help -- present but never competing for the eye.
    "quiet": {"fill": (0, 0, 0, 0), "down": (0, 0, 0, 0.07),
              "text": GROUND, "outline": (0, 0, 0, 0)},
    "danger": {"fill": DANGER, "down": DANGER_PRESSED,
               "text": ON_FILL, "outline": (0, 0, 0, 0)},
    # Sits on top of the running game, so it needs a solid backing to stay
    # readable over hills and dinos.
    "overlay": {"fill": (1.0, 1.0, 1.0, 0.88), "down": (0.88, 0.88, 0.88, 0.95),
                "text": FG, "outline": (0, 0, 0, 0.10)},
}

# ---------------------------------------------------------------------------
# Type
# ---------------------------------------------------------------------------

# Sized against the real constraint: a phone in landscape gives roughly 390dp
# of HEIGHT (1080px at ~2.75 density). A title plus four buttons has to live
# inside that, so the vertical budget is tight even though the screen is wide.
#
# The floor is 14sp for anything that reads as a sentence. It used to be 11-12,
# which is where the HUD's "620 m - 32 km/h" and every button subtitle lived --
# small enough that a phone at arm's length in motion could not resolve them,
# and small enough to fail Android's own accessibility guidance.
#
# sp() also scales with the user's system font-size setting, so a player on
# "large text" gets 1.3x of all of these. Nothing may be sized to fit exactly.
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

RADIUS = dp(14)
RADIUS_SM = dp(9)
BORDER_WIDTH = dp(1.1)

# Android's minimum recommended touch target is 48dp. Menu buttons sit above
# it -- they are hit with a thumb while gripping the phone in landscape -- but
# not so far above that four of them stop fitting on screen.
TOUCH_MIN = dp(48)
BUTTON_HEIGHT = dp(56)
BUTTON_HEIGHT_SMALL = dp(48)

# Menus are a centred column, not full-bleed: stretched across a 20:9 phone in
# landscape a single button becomes an absurd 2000px-wide bar.
CONTENT_MAX_WIDTH = dp(520)

ROW_HEIGHT = dp(48)  # a roster line, at the touch-target floor
CARD_PAD = SPACE_3
