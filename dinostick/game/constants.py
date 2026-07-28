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

# The box every character occupies, and the only thing they collide with.
#
# Scaled up ~35% from the original 44 x 60 once the painted seasons went in
# behind them: against a plain background a 60px dino read fine, but next to
# 200px palm trees it read as an ant. Everything about a character is
# normalised to this box (see SKINS), so these three numbers are the whole
# change -- no art was touched.
#
# Standing height is close to free: a grounded dino already overlaps every
# obstacle that can reach it, and clearing one is decided by where its FEET
# are, so making it taller changes no collision. The ceiling is on the DUCK
# height, which has to stay under BIRD_HIGH's underside at y=46 or ducking
# stops being an answer to it. 42 keeps 4px of daylight; much past that and
# the bird geometry in OBSTACLE_Y has to be retuned with it.
#
# Width is the one that costs anything at all: a wider dino overlaps an
# obstacle's x-span for longer, so the window of jump timings that survive
# gets narrower. Measured against every obstacle at MAX_SPEED, the worst case
# is BIRD_HIGH at -2.9% (0.501s of leeway down to 0.487s), and the jump arc
# is so much longer than the overlap that nothing comes close to unclearable.
PLAYER_WIDTH: Final[float] = 60.0
PLAYER_HEIGHT: Final[float] = 81.0
PLAYER_DUCK_HEIGHT: Final[float] = 42.0

# Fixed on-screen x positions: players are evenly spaced left->right.
PLAYER_START_X: Final[float] = 140.0
PLAYER_SPACING_X: Final[float] = 110.0

MIN_PLAYERS: Final[int] = 1
MAX_PLAYERS: Final[int] = 4

# The player whose device is hosting is always id 0. Lives here rather than in
# net/host.py because the simulation side needs it too -- the joiner steers its
# world clock by the host's tick, and it has to know whose tick that is.
HOST_PLAYER_ID: Final[int] = 0

# ---------------------------------------------------------------------------
# Elastic rope  (Phase 2 -- tuned there for feel)
# ---------------------------------------------------------------------------

# The rope tethers players; it is not supposed to drive them. Each person
# plays their own run, reacting to what is in front of their own dino, and
# feels the others as weight. Failure is shared -- one crash ends the run for
# everyone (Simulation._check_collisions) -- so the tension is social, not
# mechanical.
#
# The original tuning (K=16, MAX_STRETCH=40) contradicted the game's own
# geometry. Players stand PLAYER_SPACING_X apart, so the same cactus reaches
# them at different times and they are FORCED to jump out of sync:
#
#   at BASE_SPEED ..... 262 ms apart
#   at 700 px/s ....... 157 ms apart
#   at MAX_SPEED ...... 100 ms apart
#
# ...while the rope's cliff sat at ~150 ms. Correct play was punished from the
# first obstacle: two bots each playing their own game averaged 18 m against
# 371 m solo -- the rope was eating 95% of the run.
#
# Retuned against that geometry. The apex each dino reaches when the second
# jumps late, free jump being 196 px and a large cactus 82 px:
#
#   desync      before        after
#     0 ms .... 196 ....... 196     in sync, rope never engages
#   157 ms ....  77 XX .... 164     the forced offset at speed
#   262 ms ....  77 XX .... 151     the forced offset at the start
#   500 ms ....  83 ....... 151     properly out of sync, still survivable
#                (XX = clips a large cactus: an unavoidable death)
#
# Being 262 ms apart now costs 45 px of jump height -- a quarter of your jump,
# clearly felt as the rope holding you back -- instead of costing the run. Two
# independent bots now average 346 m, 93% of solo.
K_ROPE: Final[float] = 6.0  # spring constant on vertical separation
ROPE_DAMP: Final[float] = 0.2  # damping on relative vertical velocity
K_ROPE: Final[float] = 6.0  # spring constant on vertical separation
ROPE_DAMP: Final[float] = 0.2  # damping on relative vertical velocity

# The ceiling is physical: two dinos can never be more than one jump height
# apart (~196 px, one at apex and one on the floor), so anything at or above
# that is unreachable and the stiffening branch below would be dead code.
# 170 leaves a live band at the very top of what is possible.
#
# It is also the denominator of rope_tension(), which drives the HUD meter and
# the screen shake -- so it is a UI number as much as a physics one. Measured
# over 12 bot runs, the cost of setting it too low:
#
#   MAX_STRETCH   meter pinned red   screen shakes
#         90            23.8%          67 / min     unreadable, shaking constantly
#        120            13.3%          67 / min
#        150             4.5%          37 / min
#        170             1.1%          16 / min     <- chosen
#        196             0.1%           5 / min     stiffening never engages
MAX_STRETCH: Final[float] = 170.0  # px beyond which the rope stiffens hard
STRETCH_STIFFNESS: Final[float] = 6.0  # multiplier past MAX_STRETCH

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
#   dino standing = 0..81      dino ducking = 0..42
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

# An icon on each disc, so what you are about to grab is readable at a glance
# instead of being a colour you have to have memorised. Colour still carries
# the meaning; the icon is what makes it learnable.
#
# Same idiom as the characters: parts in coordinates NORMALISED to the
# power-up's box, so an icon cannot drift out of its disc and the whole set
# rescales with POWERUP_SIZE. Kinds:
#
#   rect / ellipse   "r": [x, y, w, h]
#   tri              "p": [x1, y1, x2, y2, x3, y3]
#   ring             "c": [centre x, centre y, radius, stroke width]
#   star             "s": [centre x, centre y, outer r, inner r, points]
#
# Drawn in one ink colour picked for contrast against the disc -- see
# Renderer._draw_powerup -- so nothing here names a colour.
POWERUP_ICONS: Final[dict[str, tuple[dict, ...]]] = {
    # A crest: flat shoulders tapering to a point.
    POWERUP_SHIELD: (
        {"k": "rect", "r": [0.30, 0.46, 0.40, 0.26]},
        {"k": "tri", "p": [0.30, 0.46, 0.70, 0.46, 0.50, 0.22]},
    ),
    # An hourglass -- time, which is what slow-mo actually buys you.
    POWERUP_SLOWMO: (
        {"k": "tri", "p": [0.32, 0.72, 0.68, 0.72, 0.50, 0.50]},
        {"k": "tri", "p": [0.32, 0.28, 0.68, 0.28, 0.50, 0.50]},
        {"k": "rect", "r": [0.28, 0.72, 0.44, 0.06]},
        {"k": "rect", "r": [0.28, 0.22, 0.44, 0.06]},
    ),
    # An up arrow. A literal feather is unreadable at 34px; "you go up" is
    # the effect anyway.
    POWERUP_FEATHER: (
        {"k": "tri", "p": [0.26, 0.52, 0.74, 0.52, 0.50, 0.80]},
        {"k": "rect", "r": [0.43, 0.20, 0.14, 0.34]},
    ),
    # Two interlocking links -- the rope, and the two of you on it. The stroke
    # has to stay well under the radius or the links fill in and read as a
    # pair of dots.
    POWERUP_SYNC: (
        {"k": "ring", "c": [0.33, 0.50, 0.20, 0.045]},
        {"k": "ring", "c": [0.67, 0.50, 0.20, 0.045]},
    ),
    POWERUP_STAR: (
        {"k": "star", "s": [0.50, 0.50, 0.42, 0.175, 5]},
    ),
}

# Ring drawn around a power-up disc, in design px. A flat disc of colour
# vanished into the painted seasons; a darker outline pins it to the front.
POWERUP_RING: Final[float] = 2.5

# Icon ink. Which one is used is decided by the disc's brightness, so the
# yellow Star does not end up with a white icon on it.
COLOR_ICON_LIGHT: Final[tuple[float, float, float, float]] = (1.0, 1.0, 1.0, 0.95)
COLOR_ICON_DARK: Final[tuple[float, float, float, float]] = (0.16, 0.14, 0.10, 0.95)
ICON_DARK_ABOVE: Final[float] = 0.62  # perceived luminance of the disc

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

# Three ports, three jobs. Discovery and the gameplay stream are both UDP but
# must not share a port: discovery is broadcast to the whole subnet, and a
# neighbouring game's announcements landing in the snapshot decoder is exactly
# the kind of bug that only shows up at a LAN party.
PORT_DISCOVERY: Final[int] = 50505  # UDP broadcast, host announcements
PORT_GAME: Final[int] = 50506  # TCP, reliable control messages
PORT_GAME_UDP: Final[int] = 50507  # UDP, the gameplay stream

DISCOVERY_INTERVAL: Final[float] = 1.0  # s between host announcements
DISCOVERY_TIMEOUT: Final[float] = 4.0  # s before a seen host is forgotten

# ---------------------------------------------------------------------------
# Transport split
# ---------------------------------------------------------------------------
#
# Control (join, lobby, ready, skin, start, gameover, rematch, crash) goes over
# TCP: it is rare, and every message matters. Gameplay (input, state
# snapshots) goes over UDP, because it is the opposite on both counts -- 40-60
# packets a second, each one superseding the last.
#
# The reason is head-of-line blocking. TCP delivers in order, so one lost
# snapshot stalls every snapshot behind it until the retransmit lands ~1 RTT
# later. For a stream where the newest packet makes all older ones irrelevant,
# waiting for a retransmit is strictly worse than skipping it: the game freezes
# to redeliver a frame nobody wants any more. That is the lag spike.
#
# Flip this to fall back to the all-TCP path if the UDP route misbehaves --
# both are kept working.
USE_UDP_GAMEPLAY: Final[bool] = True

# The gameplay stream's rate, format and interpolation constants all live in
# net/statepacket.py, next to the code that packs the packets -- NET_TICK_HZ,
# INTERP_DELAY, EXTRAP_MAX, PEER_BUFFER, POS_SCALE. They are tunables for one
# module rather than facts about the game, and splitting them across two files
# is how a quantisation scale ends up disagreeing with the struct that uses it.

# Datagrams carry no length prefix -- UDP preserves message boundaries -- but
# an oversized one fragments at the IP layer, where losing any fragment loses
# the whole packet. A STATE packet is 15 bytes, so this ceiling now only ever
# guards a hand-rolled JSON datagram (discovery announcements).
MAX_DATAGRAM_BYTES: Final[int] = 1200

# How often a client re-announces its UDP endpoint until snapshots start
# arriving. Registration is one datagram and datagrams get lost; without a
# retry, one unlucky packet leaves that player watching a frozen world.
UDP_REGISTER_INTERVAL: Final[float] = 0.25

# Deliberate network abuse, for testing. A LAN loses almost nothing, so the
# only way to find out whether the game degrades gracefully or falls over is to
# break it on purpose. These wrap the OUTBOUND state stream (net/peers.py,
# LossyLink), which is the honest place for it: an inbound-only drop cannot
# reproduce jitter or reordering, and reordering is what exercises the seq
# filter.
#
# All ship at 0.0 and cost one comparison per packet when off. Set NET_SIM_DROP
# to 0.15 and NET_SIM_JITTER to 0.08 and the partner should stay smooth (brief
# extrapolation, no teleporting) while your own dino is completely unaffected --
# it never touches the network. See NETCODE.md.
NET_SIM_DROP: Final[float] = 0.0  # fraction of state packets thrown away
NET_SIM_LATENCY: Final[float] = 0.0  # s of added one-way delay
NET_SIM_JITTER: Final[float] = 0.0  # s of +- randomness on that delay

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

# ---------------------------------------------------------------------------
# Peer-authoritative co-op
# ---------------------------------------------------------------------------
#
# Every device simulates its OWN dino and nothing else's. Input moves your dino
# on the frame you pressed it -- there is no round trip in the loop and no
# correction afterwards, because there is no other authority to be corrected
# by. Partners arrive as a stream of snapshots and are drawn INTERP_DELAY in
# the past (net/statepacket.py, net/peers.py).
#
# That works because this is co-op. Nobody gains by lying about where their own
# dino is: the only thing you could cheat yourself into is dying. A competitive
# game would need the host to re-simulate and overrule, and would pay input lag
# for it.
#
# The world is the other half. Obstacles are a pure function of (seed, tick) --
# see game/world.py -- so both devices spawn the same cactus in the same place
# from the seed alone, and not one byte of obstacle data is ever sent.

# How far ahead of the current tick a shared world event is scheduled to take
# effect. Power-ups change how fast the world scrolls, which changes where the
# next obstacle spawns, so both devices have to apply one on the SAME tick or
# their worlds quietly drift apart. Applying it "now" cannot do that -- "now"
# is a different tick on each device. So the host names a tick far enough ahead
# that the message beats it there.
#
# 12 ticks is 200 ms, which covers a LAN round trip several times over. A
# device that still receives it late fast-forwards (CoopSession._apply_effect),
# so the lead is a smoothness margin, not a correctness requirement.
WORLD_EVENT_LEAD: Final[int] = 12

# Seconds between the host sending START and world tick 0 on both devices. It
# buys the message time to cross the network, so the run begins together rather
# than half a round trip apart -- in a world where the tick number *is* the
# obstacle layout, starting late means being permanently offset.
#
# Short enough to read as the game starting, long enough to cover a LAN trip
# many times over. The joiner subtracts its own measured latency from it.
START_COUNTDOWN: Final[float] = 0.4

# The joiner eases its world clock toward the host's rather than free-running.
# Two devices stepping 60 Hz off their own wall clocks drift a tick every few
# seconds, and a tick of drift is ~10 px of obstacle offset between the two
# screens. Nobody dies of it (each device rules on its own collisions) but it
# is visible if a partner appears to clear a cactus by a hair on your screen.
#
# Corrections are one tick per frame, so at 60 fps a 20-tick gap closes in a
# third of a second and no single frame ever jumps. The deadband stops it
# hunting either side of the truth on ordinary jitter.
WORLD_SYNC_DEADBAND: Final[int] = 2  # ticks of disagreement to ignore
WORLD_SYNC_SNAP: Final[int] = 45  # beyond this, jump rather than ease

# How often the host restates the shared score, reliably. Score is derived from
# distance and distance is derived from the tick, so both devices compute it
# independently and agree -- this is a cheap belt-and-braces resync that also
# corrects any drift the tick sync left behind.
SCORE_SYNC_INTERVAL: Final[float] = 1.0
# Distance disagreement worth correcting, in px. Below this it is a rounding
# artefact; above it the difficulty curves are meaningfully out of step.
SCORE_SYNC_TOLERANCE: Final[float] = 24.0

# Silence from a peer's state stream past this and the rope goes slack for
# them. It is much shorter than CONNECTION_TIMEOUT because the two answer
# different questions: this one is "should a frozen dino still be able to drag
# me into a cactus?" (no, almost immediately), while CONNECTION_TIMEOUT is
# "has this player left?" (a much bigger claim, made much more slowly).
PEER_ROPE_TIMEOUT: Final[float] = 1.0

# How far a dino is dimmed once its owner has gone quiet. Dark enough to read
# as "something is wrong with them", not so dark it looks like a rendering bug
# or hides a dino you still have to avoid landing on.
DISCONNECTED_FADE: Final[float] = 0.45

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
EYE_SIZE: Final[float] = 8.0  # scales with the character box, not with it
SHIELD_RING_PAD: Final[float] = 8.0  # gap between a dino and its shield ring

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
# Droop when slack. Raised with MAX_STRETCH: a rope with four times the give
# has to LOOK like it has slack to spare, or players read the old taut line as
# "we are already at the limit" and keep trying to jump in lockstep. The
# renderer still clamps this so the curve never sinks through the ground.
ROPE_SAG_MAX: Final[float] = 60.0  # px of droop when fully slack
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

# ---------------------------------------------------------------------------
# Season backdrop
# ---------------------------------------------------------------------------
#
# The hills above are the fallback. The real background is a strip of painted
# season art scrolling right to left behind the runway, and it is driven by
# DISTANCE rather than by elapsed time or an accumulator, so every mode --
# local, host and client -- shows the same season at the same metre mark
# without any of it ever going on the wire. See game/backdrop.py.

BG_DIR_NAME: Final[str] = "Dino-Stick BG pics"

# The cycle, in order: (file, is this a transition?). Seasons hold for
# BG_SEASON_METRES and repeat; a transition is laid down exactly once, so it
# sweeps past a single time on its way to the next season. After the last one
# (8T1) the cycle wraps back to season 1.
BG_SEQUENCE: Final[tuple[tuple[str, bool], ...]] = (
    ("1-Start-Season.png", False),
    ("1T2.png", True),
    ("2-Rainy-Season.png", False),
    ("2T3.png", True),
    ("3-Snowy-Season.png", False),
    ("3T4.png", True),
    ("4-Mid-Season.png", False),
    ("4T5.png", True),
    ("5-Desert-Season.png", False),
    ("5T6.png", True),
    ("6-Astroid-Season.png", False),
    ("6T7.png", True),
    ("7-Ashy-Season.png", False),
    ("7T8.png", True),
    ("8-Grassland-Season.png", False),
    ("8T1.png", True),
)

BG_SEASON_METRES: Final[float] = 500.0
# How much ground the team covers while one transition image makes its single
# pass. Fixed in METRES rather than derived from the image width so the whole
# cycle is a pure function of distance: two players on differently shaped
# screens are in the same season at the same metre mark.
BG_TRANSITION_METRES: Final[float] = 100.0

# Fraction of world speed the backdrop scrolls at. Below 1.0 because it is
# scenery on the horizon: matching the runway exactly makes distant mountains
# read as a wall sliding past a foot from your face. Approximate -- each
# season nudges it so a whole number of copies lands in exactly 500 m, which
# is what keeps the tiling seamless right up to the transition.
BG_PARALLAX: Final[float] = 0.45

# Where each image's own painted ground line sits, as a fraction of its height
# measured up from the bottom. Every image is dropped so that line lands on
# GROUND_Y -- the line the dinos actually run along -- which is what stops the
# team floating above the painted ground or sinking into it, and what keeps
# the ground continuous across a seam between two different images.
#
# Measured off the art, not guessed: it is the strongest horizontal edge in
# the bottom third of each image, which is exactly where the surface meets the
# dirt/rock band. They are not all the same -- the rainy and desert scenes put
# their waterline and sand noticeably lower than the jungle puts its grass --
# and a single shared value left a visible step in the ground at every
# transition.
BG_GROUND_FRACTIONS: Final[dict[str, float]] = {
    "1-Start-Season.png": 0.121,
    "1T2.png": 0.103,
    "2-Rainy-Season.png": 0.081,
    "2T3.png": 0.088,
    "3-Snowy-Season.png": 0.133,
    "3T4.png": 0.111,
    "4-Mid-Season.png": 0.121,
    "4T5.png": 0.118,
    "5-Desert-Season.png": 0.091,
    "5T6.png": 0.122,
    "6-Astroid-Season.png": 0.127,
    "6T7.png": 0.111,
    "7-Ashy-Season.png": 0.111,
    "7T8.png": 0.111,
    "8-Grassland-Season.png": 0.115,
    "8T1.png": 0.108,
}
# Only the OFFSET is per image; the height is shared, or the world would zoom
# in and out by a third from one season to the next. This is the fraction the
# shared height is sized against: an image whose own fraction is smaller would
# not reach the bottom edge of the screen, so it gets nudged down instead of
# leaving a strip of bare background under it (see Backdrop.tiles). Lowering
# this cures those nudges at the cost of magnifying every season.
BG_GROUND_FRACTION: Final[float] = 0.10
BG_GROUND_FRACTION_MAX: Final[float] = 0.14  # tallest sky above a ground line

# Used for layout in the frame or two before an image has finished decoding,
# so the strip does not jolt sideways when the real aspect ratio arrives.
BG_FALLBACK_ASPECT: Final[float] = 3.0
# Anything squarer than this is not one of our backgrounds -- it is Kivy's
# "loading" or "error" placeholder, which must never be drawn as scenery.
BG_MIN_ASPECT: Final[float] = 1.5

# Textures held at once. Three segments can be on screen together, and one
# more is kept warm ahead of the player; the rest are dropped so a long run
# does not end up holding all sixteen in video memory.
BG_CACHE: Final[int] = 6
# How far ahead the next image starts decoding. Loading is asynchronous, so
# this only has to beat the decode, not the whole segment.
BG_PREFETCH_METRES: Final[float] = 80.0

# Nearest-neighbour magnification: the art is pixel art, and it is drawn
# roughly 2x, where smoothing turns crisp pixels into mush.
BG_PIXEL_ART: Final[bool] = True

# The renderer scrolls the backdrop with its own smoothed metre count, because
# a client's distance arrives in ~20 Hz steps and stepping the scenery at
# snapshot rate is visible as a stutter. It advances locally at the world
# speed and eases toward the authoritative figure at this rate (per second)...
BG_CORRECT_RATE: Final[float] = 2.0
# ...but a gap this large is not lag, it is a different run (or a client that
# just joined one in progress), so the scenery jumps straight there.
BG_RESYNC_METRES: Final[float] = 15.0

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
