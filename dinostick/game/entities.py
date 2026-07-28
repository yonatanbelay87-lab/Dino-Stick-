"""Game entity dataclasses.

Pure data + geometry helpers, no Kivy and no behaviour beyond hitboxes.
Fleshed out across Phases 1-4; the shapes below are the Phase 0 contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import constants as C


@dataclass
class Rect:
    """Axis-aligned box used for collision. ``x``/``y`` are the bottom-left."""

    x: float
    y: float
    w: float
    h: float

    def overlaps(self, other: "Rect") -> bool:
        return (
            self.x < other.x + other.w
            and other.x < self.x + self.w
            and self.y < other.y + other.h
            and other.y < self.y + self.h
        )


@dataclass
class Player:
    """One dino. ``y`` is height above the ground line; up is positive."""

    id: int
    name: str = "Player"
    skin: int = 0

    x: float = 0.0
    y: float = 0.0
    vy: float = 0.0
    grounded: bool = True
    ducking: bool = False
    alive: bool = True

    # Input latched for the current tick, read straight off this device's
    # keyboard or touch zones for the dino it owns.
    want_jump: bool = False
    want_duck: bool = False

    # True for a dino owned by another device. Remote dinos are PUPPETS here:
    # their position is written from the peer's own snapshots every frame and
    # this device never integrates them. They are still full participants in
    # the rope, which reads their y and vy -- it just does not get to move
    # them, because the only authority on where they are is the device whose
    # player is holding the controls.
    remote: bool = False

    # Cleared when a peer's state stream goes quiet (PEER_ROPE_TIMEOUT). A
    # disconnected dino's rope goes slack: a frozen partner that still pulls is
    # a frozen partner that drags the team into the next cactus.
    connected: bool = True

    # Jump is edge-triggered: holding the key down must not auto-hop, so the
    # sim compares want_jump against its value last tick.
    prev_jump: bool = False

    # Time since the player last left the ground, for COYOTE_TIME grace.
    airborne_time: float = 0.0

    def hitbox(self) -> Rect:
        h = C.PLAYER_DUCK_HEIGHT if self.ducking else C.PLAYER_HEIGHT
        return Rect(self.x, self.y, C.PLAYER_WIDTH, h)


@dataclass
class Obstacle:
    """Something to jump over or duck under. Scrolls right -> left."""

    x: float
    y: float
    kind: str = "CACTUS_SMALL"
    # Stable identity for the lifetime of this obstacle. Clients interpolate
    # between two snapshots by matching ids; matching by list position breaks
    # the moment one obstacle despawns between the two.
    oid: int = 0

    def hitbox(self) -> Rect:
        w, h = C.OBSTACLE_SIZES[self.kind]
        return Rect(self.x, self.y, w, h)


@dataclass
class PowerUp:
    """Team-wide pickup: whoever touches it, the whole team gets the effect."""

    x: float
    y: float
    kind: str = "SHIELD"
    pid: int = 0  # stable id, same purpose as Obstacle.oid

    def hitbox(self) -> Rect:
        w, h = C.POWERUP_SIZE
        return Rect(self.x, self.y, w, h)


@dataclass
class Modifiers:
    """Team-wide multipliers produced by active power-ups.

    Physics reads these instead of the raw constants so an effect can bend
    gravity or slacken the rope without anything else having to know why.
    """

    gravity_scale: float = 1.0
    rope_scale: float = 1.0
    speed_scale: float = 1.0


@dataclass
class GameState:
    """A full snapshot of the world -- what the host simulates and broadcasts."""

    tick: int = 0
    players: list[Player] = field(default_factory=list)
    obstacles: list[Obstacle] = field(default_factory=list)
    powerups: list[PowerUp] = field(default_factory=list)

    distance: float = 0.0
    score: int = 0
    # Points from pickups. Kept apart from distance so the running total can be
    # recomputed every tick without a Star bonus being overwritten by it.
    bonus: int = 0
    speed: float = C.BASE_SPEED
    running: bool = False

    # Active team-wide effects: kind -> seconds remaining.
    effects: dict[str, float] = field(default_factory=dict)
    # Shield has no timer: it sits there until it eats one fatal hit.
    shield: bool = False

    # Transient things that happened this tick, for sound and particles.
    # Cleared at the top of every step(); consumers must read them promptly.
    events: list[dict] = field(default_factory=list)

    # Set when the team wipes: which player hit what.
    cause_player_id: int | None = None
    cause_obstacle: str | None = None


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
#
# There is deliberately no state_to_snapshot / snapshot_to_state pair here any
# more, and no interpolate(). Those served the host-authoritative model, where
# one device serialised the whole world -- obstacles, power-ups, score, every
# player -- and the others rendered a blend of the last two copies of it.
#
# Under peer authority there is no whole world to serialise. Each device sends
# only its own dino, as 15 packed bytes (net/statepacket.py), and the obstacles
# both devices see come from the shared seed rather than from the wire.
# Partner interpolation moved to net/peers.py, where it operates on those
# packets instead of on GameState.
