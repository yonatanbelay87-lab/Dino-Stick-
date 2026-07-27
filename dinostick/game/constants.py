"""All tunables for Dino Stick.

Every magic number in the game lives here: physics, rope constants, world
generation, networking ports/timings, and the colour palette. Nothing in
``game/`` or ``net/`` should hard-code a numeric tunable.

Values marked ``# Phase N`` are not exercised until that phase lands, but are
defined now so importers never have to guard for absence.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

APP_NAME: Final[str] = "Dino Stick"
APP_VERSION: Final[str] = "0.1.0"

# ---------------------------------------------------------------------------
# Simulation timing
# ---------------------------------------------------------------------------

# The simulation runs at a fixed rate, decoupled from the render framerate.
# The Kivy Clock callback accumulates real elapsed time and steps the sim in
# whole TICK_DT slices (see screens/game.py).
TICK_HZ: Final[int] = 60
TICK_DT: Final[float] = 1.0 / TICK_HZ

# Safety valve for the accumulator: if the app is suspended / the window is
# dragged, never try to catch up more than this many ticks in one frame.
MAX_TICKS_PER_FRAME: Final[int] = 5

# Ignore any single frame delta longer than this (alt-tab, debugger pause).
MAX_FRAME_DT: Final[float] = 0.25

# ---------------------------------------------------------------------------
# Player physics  (ground is y = 0, up is positive)
# ---------------------------------------------------------------------------

GRAVITY: Final[float] = -2400.0  # px/s^2
JUMP_VELOCITY: Final[float] = 950.0  # px/s
COYOTE_TIME: Final[float] = 0.08  # s of grace after leaving the ground

# Holding duck while airborne pulls you down faster. This is the only way to
# shorten a jump you have already committed to -- and from Phase 2 it is how
# you stop yanking your rope partners after an early jump.
DUCK_FALL_MULTIPLIER: Final[float] = 2.2

# Derived: total hang time of a full jump, and its apex height. The spawner
# uses these to guarantee it never places two obstacles closer together than
# a single jump arc, at any scroll speed.
JUMP_AIR_TIME: Final[float] = 2.0 * JUMP_VELOCITY / abs(GRAVITY)  # ~0.79 s
JUMP_PEAK_HEIGHT: Final[float] = JUMP_VELOCITY**2 / (2.0 * abs(GRAVITY))  # ~188 px

PLAYER_WIDTH: Final[float] = 44.0
PLAYER_HEIGHT: Final[float] = 60.0
PLAYER_DUCK_HEIGHT: Final[float] = 32.0

# Fixed on-screen x positions: players are evenly spaced left->right.
PLAYER_START_X: Final[float] = 140.0
PLAYER_SPACING_X: Final[float] = 110.0

MIN_PLAYERS: Final[int] = 1
MAX_PLAYERS: Final[int] = 4

# ---------------------------------------------------------------------------
# Elastic rope  (Phase 2 -- tuned there for feel)
# ---------------------------------------------------------------------------

# Tuned in Phase 2 against a measured response curve, not by eye. The shape we
# want is "slack, then a wall": nearly silent when partners jump together, and
# a hard yank once they drift apart.
#
#   desync between two jumps  ->  apexes reached (free jump is 188 px)
#     0 ms .................... 196 / 196   rope never engages
#    50 ms .................... 167 / 212   slight tug, partner slingshotted
#   100 ms .................... 173 / 174   still clears a large cactus
#   167 ms ..................... 83 /  77   CLIFF: neither clears a large cactus
#   solo jump .................. 83 /  77   partner ripped 77 px off the floor
#
# Past ~167 ms the late player has already been yanked airborne, so their jump
# input is silently eaten -- they cannot jump because they are not grounded.
# That is the punish. By ~500 ms they have landed and can act again, which is
# what keeps it recoverable.
K_ROPE: Final[float] = 16.0  # spring constant on vertical separation
ROPE_DAMP: Final[float] = 0.3  # damping on relative vertical velocity

# NB: this is far below the 220 the design sketch suggested, and it has to be.
# Two dinos can never be more than one jump height apart (~196 px, one at apex
# and one on the floor), so a 220 px limit is physically unreachable and the
# stiffening branch would be dead code. At 40 px the rope has a short elastic
# toe and then genuinely runs out of give -- which is what makes it read as a
# rope rather than a rubber band.
MAX_STRETCH: Final[float] = 40.0  # px beyond which the rope stiffens hard
STRETCH_STIFFNESS: Final[float] = 14.0  # multiplier past MAX_STRETCH

# ---------------------------------------------------------------------------
# World / difficulty ramp
# ---------------------------------------------------------------------------

BASE_SPEED: Final[float] = 420.0  # px/s at distance 0
# speed = BASE_SPEED + SPEED_K * sqrt(distance), capped at MAX_SPEED.
# SPEED_K is set so the cap lands around the 3-4 minute mark: at 6.0 the run
# maxed out inside 30 s and every run after that felt identical.
SPEED_K: Final[float] = 1.7
MAX_SPEED: Final[float] = 1100.0

SPAWN_X: Final[float] = 1400.0  # x at which obstacles enter the world
DESPAWN_X: Final[float] = -200.0  # x past which they are culled

FIRST_SPAWN_DELAY: Final[float] = 1.5  # s of clear runway at the start of a run

# Gap between obstacles, as a MULTIPLE OF ONE JUMP ARC (JUMP_AIR_TIME).
# Expressing it this way is what keeps the game fair: a gap of 1.0 is exactly
# the distance covered by one full jump, so anything above 1.0 is landable no
# matter how fast the world is scrolling. The EASY pair is used at BASE_SPEED
# and the HARD pair at MAX_SPEED, lerped by the difficulty factor in between --
# so the spawn interval does shrink as speed rises, but never past clearable.
# The floor is 1.25, not 1.0-and-a-bit. A gap of exactly one arc means landing
# on the frame the next obstacle arrives, with no time to time a jump -- fair
# on paper, unplayable in practice. Phase 4's wider obstacles made that margin
# bite: a jump-and-duck bot fell from 25/25 to 15/25 at a 1.05 floor.
GAP_MIN_EASY: Final[float] = 1.45
GAP_MAX_EASY: Final[float] = 2.40
GAP_MIN_HARD: Final[float] = 1.25
GAP_MAX_HARD: Final[float] = 1.65

# Odds of the taller cactus, lerped over the same difficulty factor.
CACTUS_LARGE_CHANCE_EASY: Final[float] = 0.30
CACTUS_LARGE_CHANCE_HARD: Final[float] = 0.55

SCORE_PER_PIXEL: Final[float] = 0.02

# ---------------------------------------------------------------------------
# Obstacles  (expanded with birds / rocks / clusters in Phase 4)
# ---------------------------------------------------------------------------

OBSTACLE_SIZES: Final[dict[str, tuple[float, float]]] = {
    "CACTUS_SMALL": (26.0, 52.0),
    "CACTUS_LARGE": (38.0, 82.0),
    "CACTUS_CLUSTER": (78.0, 64.0),
    "ROCK": (46.0, 40.0),
    "BIRD_LOW": (46.0, 34.0),
    "BIRD_HIGH": (46.0, 40.0),
}

# Height off the ground. Ground obstacles sit at 0; birds fly.
#
# The two bird heights are chosen against the jump apexes measured in Phase 2,
# because "duck under this" only means anything if jumping is a bad idea:
#
#   dino standing = 0..60      dino ducking = 0..32
#   solo jump apex = 83        perfectly synced jump apex = 196
#
#   BIRD_LOW  occupies 26..60 -> hits you standing AND ducking, so you must
#                               jump; clearing 60 works even on a solo jump.
#   BIRD_HIGH occupies 46..86 -> hits you standing, misses you ducking, and
#                               clearing 86 is beyond a solo jump (83) but
#                               inside a synced one. Ducking is the reliable
#                               answer; jumping it is a coordinated gamble.
#
# Ducking needs both feet down, so a bird arriving while the rope has you
# airborne is lethal -- which is the point.
OBSTACLE_Y: Final[dict[str, float]] = {
    "CACTUS_SMALL": 0.0,
    "CACTUS_LARGE": 0.0,
    "CACTUS_CLUSTER": 0.0,
    "ROCK": 0.0,
    "BIRD_LOW": 26.0,
    "BIRD_HIGH": 46.0,
}

# What the obstacle demands of you. Used by tests and the how-to-play text.
OBSTACLE_ACTION: Final[dict[str, str]] = {
    "CACTUS_SMALL": "jump",
    "CACTUS_LARGE": "jump",
    "CACTUS_CLUSTER": "jump",
    "ROCK": "jump",
    "BIRD_LOW": "jump",
    "BIRD_HIGH": "duck",
}

# Spawn weighting: (weight at BASE_SPEED, weight at MAX_SPEED, unlock point).
# Harder types fade in as the run goes on rather than appearing at second one.
OBSTACLE_WEIGHTS: Final[dict[str, tuple[float, float, float]]] = {
    "CACTUS_SMALL": (34.0, 12.0, 0.00),
    "CACTUS_LARGE": (22.0, 20.0, 0.00),
    "ROCK": (16.0, 14.0, 0.05),
    "CACTUS_CLUSTER": (0.0, 18.0, 0.15),
    "BIRD_LOW": (0.0, 18.0, 0.30),
    "BIRD_HIGH": (0.0, 20.0, 0.45),
}

# Human-readable names for the game-over "crashed into a ..." line.
OBSTACLE_LABELS: Final[dict[str, str]] = {
    "CACTUS_SMALL": "a small cactus",
    "CACTUS_LARGE": "a large cactus",
    "CACTUS_CLUSTER": "a cactus cluster",
    "ROCK": "a boulder",
    "BIRD_LOW": "a low-flying bird",
    "BIRD_HIGH": "a bird (duck!)",
}

# ---------------------------------------------------------------------------
# Power-ups  (team-wide: the host applies them to everyone at once)
# ---------------------------------------------------------------------------

POWERUP_SHIELD: Final[str] = "SHIELD"
POWERUP_SLOWMO: Final[str] = "SLOWMO"
POWERUP_FEATHER: Final[str] = "FEATHER"
POWERUP_SYNC: Final[str] = "SYNC"
POWERUP_STAR: Final[str] = "STAR"

POWERUP_SIZE: Final[tuple[float, float]] = (34.0, 34.0)

# Heights they float at. Deliberately above a solo jump (83px) for the better
# ones, so collecting them is itself a reason to coordinate.
POWERUP_HEIGHTS: Final[tuple[float, ...]] = (70.0, 120.0, 160.0)

POWERUP_WEIGHTS: Final[dict[str, float]] = {
    POWERUP_SHIELD: 22.0,
    POWERUP_SLOWMO: 20.0,
    POWERUP_FEATHER: 18.0,
    POWERUP_SYNC: 18.0,
    POWERUP_STAR: 22.0,
}

POWERUP_INTERVAL_MIN: Final[float] = 9.0  # seconds between power-up spawns
POWERUP_INTERVAL_MAX: Final[float] = 16.0
POWERUP_FIRST_DELAY: Final[float] = 6.0

POWERUP_DURATIONS: Final[dict[str, float]] = {
    POWERUP_SLOWMO: 5.0,
    POWERUP_FEATHER: 6.0,
    POWERUP_SYNC: 6.0,
}

SLOWMO_SPEED_SCALE: Final[float] = 0.55

# Feather's real gift is the slack rope; the low gravity is mostly feel.
# Keep the gravity cut mild: hang time scales as 1/gravity_scale, and a team
# that floats for longer than the gap between obstacles cannot land between
# them at all -- at 0.55 the dinos sailed to 350px and came down onto a cactus
# with no way to intervene. The spawner also widens gaps while this is active
# (see World._next_interval), but the two have to work together.
FEATHER_GRAVITY_SCALE: Final[float] = 0.75
FEATHER_ROPE_SCALE: Final[float] = 0.35
STAR_SCORE_BONUS: Final[int] = 250

POWERUP_LABELS: Final[dict[str, str]] = {
    POWERUP_SHIELD: "Shield",
    POWERUP_SLOWMO: "Slow-mo",
    POWERUP_FEATHER: "Feather",
    POWERUP_SYNC: "Sync",
    POWERUP_STAR: "Star",
}

POWERUP_COLORS: Final[dict[str, tuple[float, float, float, float]]] = {
    POWERUP_SHIELD: (0.24, 0.60, 0.86, 1.0),
    POWERUP_SLOWMO: (0.55, 0.40, 0.80, 1.0),
    POWERUP_FEATHER: (0.30, 0.72, 0.55, 1.0),
    POWERUP_SYNC: (0.92, 0.62, 0.20, 1.0),
    POWERUP_STAR: (0.94, 0.78, 0.20, 1.0),
}

# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

# Local keyboard layout, indexed by player slot. Phase 1 only uses slot 0;
# Phase 2 brings slot 1 online for same-screen co-op, so the arrow keys are
# reserved for player 2 from the start rather than being re-bound later.
KEYBOARD_BINDINGS: Final[tuple[dict[str, tuple[str, ...]], ...]] = (
    {"jump": ("spacebar", "w"), "duck": ("s",)},
    {"jump": ("up",), "duck": ("down",)},
    {"jump": ("t",), "duck": ("g",)},
    {"jump": ("o",), "duck": ("l",)},
)

# Touch zones for phones, as fractions of the screen: (x, y, width, height)
# measured from the bottom-left. The defaults split the screen down the middle:
# right half = jump, left half = duck. Both are held actions, so multi-touch
# lets you duck and jump independently.
#
# Change these to re-map touch controls; nothing else needs to know.
TOUCH_ZONES: Final[dict[str, tuple[float, float, float, float]]] = {
    "duck": (0.0, 0.0, 0.5, 1.0),
    "jump": (0.5, 0.0, 0.5, 1.0),
}
# Fraction of screen height at the top reserved for HUD buttons (Quit), so a
# tap there does not also make the dino jump. Comfortably taller than the 48dp
# touch target itself, so a thumb that lands slightly low still misses the
# jump zone rather than launching the whole team.
TOUCH_DEAD_ZONE_TOP: Final[float] = 0.16

# How long the big DUCK / JUMP zone labels stay up at the start of a run on a
# touch device before fading out.
TOUCH_HINT_SECONDS: Final[float] = 3.5

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

PORT_DISCOVERY: Final[int] = 50505  # UDP broadcast
PORT_GAME: Final[int] = 50506  # TCP game traffic

DISCOVERY_INTERVAL: Final[float] = 1.0  # s between host announcements
DISCOVERY_TIMEOUT: Final[float] = 4.0  # s before a seen host is forgotten

STATE_BROADCAST_HZ: Final[int] = 30  # host -> clients state rate
SOCKET_TIMEOUT: Final[float] = 5.0
LENGTH_PREFIX_BYTES: Final[int] = 4  # big-endian uint32 frame header
MAX_FRAME_BYTES: Final[int] = 1 << 20  # reject absurd frames

# Clients ping this often, in every screen -- not just in game. It doubles as
# the keepalive both ends use to notice a peer that has gone away.
PING_INTERVAL: Final[float] = 1.0

# Silence longer than this means the peer is gone. TCP alone will not tell you:
# a phone that walks out of Wi-Fi range or drops into a tunnel leaves the
# socket open and perfectly healthy-looking for minutes, during which the host
# keeps broadcasting into the void and that player's dino stands still on
# everyone's screen until it kills the team. Six missed pings.
CONNECTION_TIMEOUT: Final[float] = 6.0

# Clients render slightly in the past so there is always a newer snapshot to
# interpolate toward; without this the view stalls on the last packet whenever
# one is late. Roughly two broadcast intervals is the usual safe choice.
CLIENT_RENDER_DELAY: Final[float] = 2.0 / STATE_BROADCAST_HZ
SNAPSHOT_BUFFER: Final[int] = 16  # snapshots kept for interpolation

DEFAULT_HOST_NAME: Final[str] = "Dino Stick game"

# ---------------------------------------------------------------------------
# Rendering / layout
# ---------------------------------------------------------------------------

# The simulation always runs in this fixed design space; the renderer fits it
# to whatever the real window/screen is. Keeping the sim resolution-
# independent is what lets a phone and a laptop share one authoritative world.
DESIGN_WIDTH: Final[float] = 1280.0
DESIGN_HEIGHT: Final[float] = 720.0
GROUND_Y: Final[float] = 140.0  # design-space y of the ground line

# The fit is UNIFORM -- one scale for both axes -- because a phone screen is
# not 16:9. Stretching x and y independently to fill a 20:9 panel squashed
# every dino by ~20%, and squashing is the one distortion the eye reads
# instantly on a character.
#
# Width is what gets matched, since the full design width has to stay visible:
# it is the runway an obstacle crosses before it reaches you, and cropping it
# would hand a wide phone less reaction time than a laptop. Leftover height
# becomes extra sky, which costs nothing -- nothing is drawn up there.
#
# The one thing height may not do is go SHORT. On a squat window (a dragged
# desktop window, mostly) matching the width would push the jump apex off the
# top, so the scale is capped at whatever still leaves this many design pixels
# of world visible. Covers the ground line (140), a synced jump apex plus the
# dino's own height (~256), and the highest power-up (194) with room to spare.
VIEW_MIN_HEIGHT: Final[float] = 520.0

GROUND_LINE_WIDTH: Final[float] = 2.0
EYE_SIZE: Final[float] = 6.0

# The sim counts pixels; players do not think in pixels. Everything shown to a
# human is converted through this, so the HUD reads "620 m" and "32 km/h"
# instead of "62,043 px" and "890 px/s".
#
# 100 px = 1 m is not arbitrary -- it is the ratio that makes every derived
# number land somewhere a person recognises: the dino is 0.6 m tall, a synced
# jump clears 2 m, and the run accelerates from 15 km/h to 40 km/h, which is
# jogging up to a sprint. Halve it and the dinos are man-sized giants moving at
# a walking pace; double it and the whole team is sprinting at 80 km/h.
PIXELS_PER_METRE: Final[float] = 100.0

# Rope drawing. The line sags when slack and pulls straight + thin + red as it
# approaches MAX_STRETCH, so tension is readable at a glance.
ROPE_WIDTH: Final[float] = 3.0  # slack
ROPE_WIDTH_TAUT: Final[float] = 1.6  # at full stretch
ROPE_SAG_MAX: Final[float] = 34.0  # px of droop when fully slack
ROPE_SEGMENTS: Final[int] = 14  # polyline resolution for the sag curve
ROPE_MIN_CLEARANCE: Final[float] = 6.0  # keep the droop from sinking into the ground

# ---------------------------------------------------------------------------
# Juice
# ---------------------------------------------------------------------------

SHAKE_DURATION: Final[float] = 0.35
SHAKE_MAGNITUDE: Final[float] = 14.0
# Rope tension above this, arriving suddenly, counts as a "snap" worth shaking.
SHAKE_TENSION_TRIGGER: Final[float] = 0.92

PARTICLE_COUNT: Final[int] = 18
PARTICLE_LIFETIME: Final[float] = 0.9
PARTICLE_SPEED: Final[float] = 320.0
PARTICLE_SIZE: Final[float] = 5.0
PARTICLE_GRAVITY: Final[float] = -900.0

# Parallax layers: (scroll fraction of world speed, dome height, colour).
# Drawn as half-ellipses sitting ON the ground line, so they read as distant
# hills on the horizon. Kept low and close to the background colour: the first
# pass used full ellipses 240px tall and they swamped the actual gameplay.
PARALLAX_LAYERS: Final[tuple[tuple[float, float, tuple], ...]] = (
    (0.12, 96.0, (0.913, 0.913, 0.913, 1.0)),
    (0.26, 64.0, (0.884, 0.884, 0.884, 1.0)),
    (0.46, 38.0, (0.855, 0.855, 0.855, 1.0)),
)
PARALLAX_HILL_SPACING: Final[float] = 470.0  # distance between hill centres
PARALLAX_HILL_WIDTH: Final[float] = 400.0  # width of one dome

NAMEPLATE_OFFSET: Final[float] = 16.0  # px above a dino's head

SOUND_VOLUME: Final[float] = 0.4

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

COLOR_BG: Final[tuple[float, float, float, float]] = (0.96, 0.96, 0.96, 1.0)
COLOR_FG: Final[tuple[float, float, float, float]] = (0.32, 0.32, 0.32, 1.0)
COLOR_GROUND: Final[tuple[float, float, float, float]] = (0.51, 0.51, 0.51, 1.0)
COLOR_ROPE: Final[tuple[float, float, float, float]] = (0.35, 0.30, 0.28, 1.0)
COLOR_ROPE_TAUT: Final[tuple[float, float, float, float]] = (0.85, 0.32, 0.18, 1.0)
COLOR_ACCENT: Final[tuple[float, float, float, float]] = (0.20, 0.62, 0.47, 1.0)
COLOR_DANGER: Final[tuple[float, float, float, float]] = (0.78, 0.25, 0.22, 1.0)
COLOR_EYE: Final[tuple[float, float, float, float]] = (0.96, 0.96, 0.96, 1.0)

# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------
#
# Each character is a silhouette built from simple parts, in coordinates
# NORMALISED to the player's hitbox: (0,0) is its bottom-left, (1,1) its
# top-right. Two consequences, both deliberate:
#
#   * Characters are purely cosmetic. Every one of them occupies the same
#     PLAYER_WIDTH x PLAYER_HEIGHT box, so picking a bigger-looking creature
#     can never hand the whole team a worse hitbox.
#   * Ducking squashes the whole silhouette automatically, because the hitbox
#     itself shrinks to PLAYER_DUCK_HEIGHT. No separate crouch art needed.
#
# Part shapes:
#   {"k": "rect",    "r": (x, y, w, h)}
#   {"k": "ellipse", "r": (x, y, w, h)}
#   {"k": "tri",     "p": (x1, y1, x2, y2, x3, y3)}
# "s" shades the base colour (1.0 = as-is, <1 darker, >1 lighter).
# "eye" is where the white dot goes; all characters face right, into the
# oncoming obstacles.

_REX = (
    {"k": "tri", "p": (0.00, 0.30, 0.24, 0.46, 0.24, 0.20), "s": 0.85},
    {"k": "rect", "r": (0.30, 0.00, 0.11, 0.26), "s": 0.80},
    {"k": "rect", "r": (0.52, 0.00, 0.11, 0.26), "s": 0.80},
    {"k": "rect", "r": (0.18, 0.20, 0.46, 0.42), "s": 1.0},
    {"k": "rect", "r": (0.55, 0.48, 0.33, 0.34), "s": 1.0},
    {"k": "rect", "r": (0.76, 0.52, 0.24, 0.15), "s": 0.95},
)

_RAPTOR = (
    {"k": "tri", "p": (0.00, 0.56, 0.27, 0.64, 0.27, 0.38), "s": 0.85},
    {"k": "rect", "r": (0.30, 0.00, 0.10, 0.34), "s": 0.80},
    {"k": "rect", "r": (0.50, 0.00, 0.10, 0.34), "s": 0.80},
    {"k": "rect", "r": (0.20, 0.28, 0.45, 0.32), "s": 1.0},
    {"k": "tri", "p": (0.58, 0.76, 0.72, 0.94, 0.76, 0.74), "s": 1.15},
    {"k": "rect", "r": (0.56, 0.50, 0.30, 0.26), "s": 1.0},
    {"k": "rect", "r": (0.80, 0.52, 0.20, 0.13), "s": 0.95},
)

_STEGO = (
    {"k": "tri", "p": (0.00, 0.28, 0.18, 0.44, 0.18, 0.18), "s": 0.85},
    {"k": "rect", "r": (0.26, 0.00, 0.12, 0.20), "s": 0.80},
    {"k": "rect", "r": (0.56, 0.00, 0.12, 0.20), "s": 0.80},
    {"k": "tri", "p": (0.26, 0.52, 0.36, 0.82, 0.46, 0.52), "s": 1.20},
    {"k": "tri", "p": (0.46, 0.52, 0.56, 0.88, 0.66, 0.52), "s": 1.20},
    {"k": "ellipse", "r": (0.12, 0.16, 0.66, 0.44), "s": 1.0},
    {"k": "rect", "r": (0.74, 0.26, 0.26, 0.22), "s": 1.0},
)

# Wings sweep BACK and stay left of the head. The first pass had them arcing
# forward over the face, which hid the beak entirely and made the whole
# creature read as a purple arrowhead.
_PTERO = (
    {"k": "tri", "p": (0.00, 0.30, 0.30, 0.78, 0.44, 0.36), "s": 0.80},
    {"k": "rect", "r": (0.42, 0.02, 0.07, 0.24), "s": 0.80},
    {"k": "tri", "p": (0.18, 0.30, 0.46, 0.66, 0.58, 0.36), "s": 1.14},
    {"k": "ellipse", "r": (0.32, 0.20, 0.34, 0.34), "s": 1.0},
    {"k": "tri", "p": (0.60, 0.60, 0.54, 0.84, 0.78, 0.60), "s": 1.18},
    {"k": "rect", "r": (0.60, 0.38, 0.22, 0.24), "s": 1.0},
    {"k": "tri", "p": (0.80, 0.56, 1.00, 0.47, 0.80, 0.38), "s": 0.92},
)

_CROC = (
    {"k": "tri", "p": (0.00, 0.20, 0.20, 0.34, 0.20, 0.10), "s": 0.85},
    {"k": "rect", "r": (0.22, 0.00, 0.12, 0.16), "s": 0.80},
    {"k": "rect", "r": (0.46, 0.00, 0.12, 0.16), "s": 0.80},
    {"k": "tri", "p": (0.22, 0.42, 0.29, 0.58, 0.36, 0.42), "s": 1.18},
    {"k": "tri", "p": (0.40, 0.42, 0.47, 0.58, 0.54, 0.42), "s": 1.18},
    {"k": "rect", "r": (0.14, 0.12, 0.48, 0.32), "s": 1.0},
    {"k": "rect", "r": (0.58, 0.30, 0.14, 0.16), "s": 1.0},
    {"k": "rect", "r": (0.60, 0.14, 0.40, 0.18), "s": 0.95},
)

_YETI = (
    {"k": "rect", "r": (0.28, 0.00, 0.15, 0.18), "s": 0.80},
    {"k": "rect", "r": (0.52, 0.00, 0.15, 0.18), "s": 0.80},
    {"k": "rect", "r": (0.06, 0.28, 0.18, 0.30), "s": 0.88},
    {"k": "ellipse", "r": (0.14, 0.10, 0.60, 0.58), "s": 1.0},
    {"k": "ellipse", "r": (0.44, 0.52, 0.44, 0.40), "s": 1.08},
    {"k": "rect", "r": (0.78, 0.62, 0.20, 0.14), "s": 0.95},
)

SKINS: Final[tuple[dict, ...]] = (
    {"name": "Rex", "color": (0.32, 0.32, 0.32, 1.0),
     "parts": _REX, "eye": (0.80, 0.70)},
    {"name": "Raptor", "color": (0.20, 0.55, 0.72, 1.0),
     "parts": _RAPTOR, "eye": (0.78, 0.64)},
    {"name": "Stego", "color": (0.78, 0.45, 0.20, 1.0),
     "parts": _STEGO, "eye": (0.86, 0.38)},
    {"name": "Ptero", "color": (0.45, 0.28, 0.62, 1.0),
     "parts": _PTERO, "eye": (0.70, 0.50)},
    {"name": "Croc", "color": (0.25, 0.58, 0.35, 1.0),
     "parts": _CROC, "eye": (0.63, 0.38)},
    {"name": "Yeti", "color": (0.60, 0.61, 0.70, 1.0),
     "parts": _YETI, "eye": (0.74, 0.74)},
)

# Kept as flat tuples so the lobby, HUD and nameplates can index by skin id.
SKIN_COLORS: Final[tuple[tuple[float, float, float, float], ...]] = tuple(
    s["color"] for s in SKINS)
SKIN_NAMES: Final[tuple[str, ...]] = tuple(s["name"] for s in SKINS)
