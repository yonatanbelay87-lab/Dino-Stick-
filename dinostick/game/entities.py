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

    # Input latched for the current tick (host applies the latest it has).
    want_jump: bool = False
    want_duck: bool = False

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
# Network snapshots
# ---------------------------------------------------------------------------
#
# The host broadcasts a trimmed view of GameState. Two things are deliberately
# left out because clients can reconstruct them and they would otherwise be
# resent 30 times a second:
#   * player x -- fixed per slot, derived from the player's id
#   * name / skin -- cosmetic, delivered once in the lobby/start message


def state_to_snapshot(state: GameState) -> dict:
    """Serialise the authoritative state into the ``state`` message payload."""
    return {
        "tick": state.tick,
        "players": [
            {
                "id": p.id,
                "y": round(p.y, 2),
                "vy": round(p.vy, 2),
                "grounded": p.grounded,
                "ducking": p.ducking,
                "alive": p.alive,
            }
            for p in state.players
        ],
        "obstacles": [
            {"oid": o.oid, "x": round(o.x, 2), "y": round(o.y, 2), "type": o.kind}
            for o in state.obstacles
        ],
        "powerups": [
            {"pid": pu.pid, "x": round(pu.x, 2), "y": round(pu.y, 2),
             "type": pu.kind}
            for pu in state.powerups
        ],
        "score": state.score,
        "distance": round(state.distance, 1),
        "speed": round(state.speed, 2),
        "effects": [{"kind": k, "remaining": round(v, 2)}
                    for k, v in state.effects.items()],
        "shield": state.shield,
    }


def snapshot_to_state(snap: dict, roster: dict[int, tuple[str, int]] | None = None
                      ) -> GameState:
    """Rebuild a renderable GameState from a ``state`` payload."""
    roster = roster or {}
    players = []
    for entry in snap.get("players", []):
        pid = int(entry["id"])
        name, skin = roster.get(pid, (f"P{pid + 1}", pid))
        players.append(
            Player(
                id=pid,
                name=name,
                skin=skin,
                x=C.PLAYER_START_X + pid * C.PLAYER_SPACING_X,
                y=float(entry["y"]),
                vy=float(entry.get("vy", 0.0)),
                grounded=bool(entry.get("grounded", True)),
                ducking=bool(entry.get("ducking", False)),
                alive=bool(entry.get("alive", True)),
            )
        )
    obstacles = [
        Obstacle(x=float(o["x"]), y=float(o.get("y", 0.0)),
                 kind=o["type"], oid=int(o.get("oid", 0)))
        for o in snap.get("obstacles", [])
    ]
    powerups = [
        PowerUp(x=float(p["x"]), y=float(p.get("y", 0.0)), kind=p["type"],
                pid=int(p.get("pid", 0)))
        for p in snap.get("powerups", [])
    ]
    return GameState(
        tick=int(snap.get("tick", 0)),
        players=players,
        obstacles=obstacles,
        powerups=powerups,
        score=int(snap.get("score", 0)),
        distance=float(snap.get("distance", 0.0)),
        speed=float(snap.get("speed", C.BASE_SPEED)),
        effects={e["kind"]: float(e["remaining"])
                 for e in snap.get("effects", [])},
        shield=bool(snap.get("shield", False)),
        events=list(snap.get("events", [])),
        running=True,
    )


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def select_pair(snapshots, target: float):
    """Find the two buffered snapshots that bracket ``target``.

    Returns ``(older, newer, t)`` with t running 0..1 between their arrival
    times, or ``None`` when the target falls outside everything buffered --
    which means the caller has to show the newest snapshot as-is and accept a
    visible snap.

    Pulled out of the render loop so the delay and buffer size can be swept
    against recorded arrival patterns without standing up a window. Tuning
    INTERP_DELAY_MS by eye is how you end up with a number nobody can defend.
    """
    for i in range(len(snapshots) - 1):
        older, newer = snapshots[i], snapshots[i + 1]
        if older[0] <= target <= newer[0]:
            span = newer[0] - older[0]
            t = 0.0 if span <= 0 else (target - older[0]) / span
            return older, newer, t
    return None


def interpolate(older: GameState, newer: GameState, t: float,
                skip_ids: frozenset[int] | set[int] = frozenset()
                ) -> GameState:
    """Blend two snapshots for smooth client rendering.

    ``t`` runs 0 (show ``older``) to 1 (show ``newer``). Players are matched by
    id and obstacles by ``oid``; anything present in only one of the two
    snapshots is taken as-is rather than blended, which is what stops obstacles
    from sliding in from the wrong place as they spawn and despawn.

    ``skip_ids`` names players that must not be blended -- in practice, the
    one you are controlling. Interpolation renders the past, and the past is
    the one place your own dino must never be: you press jump and expect to
    leave the ground now, not INTERP_DELAY_MS from now. That player is drawn
    from prediction instead (see the client-side prediction in screens/game).
    """
    t = max(0.0, min(1.0, t))

    old_players = {p.id: p for p in older.players}
    players = []
    for new in newer.players:
        old = old_players.get(new.id)
        if old is None or new.id in skip_ids:
            players.append(new)
            continue
        blended = Player(
            id=new.id, name=new.name, skin=new.skin, x=new.x,
            y=_lerp(old.y, new.y, t),
            vy=_lerp(old.vy, new.vy, t),
            grounded=new.grounded, ducking=new.ducking, alive=new.alive,
        )
        players.append(blended)

    old_obstacles = {o.oid: o for o in older.obstacles}
    obstacles = []
    for new in newer.obstacles:
        old = old_obstacles.get(new.oid)
        if old is None or new.oid == 0:
            obstacles.append(new)
        else:
            obstacles.append(
                Obstacle(x=_lerp(old.x, new.x, t), y=new.y,
                         kind=new.kind, oid=new.oid)
            )

    # Events are one-shot triggers for sound and particles, never blended --
    # firing the newer snapshot's events once, as it becomes current, is what
    # keeps a crash from being played twice.
    old_powerups = {p.pid: p for p in older.powerups}
    powerups = []
    for new in newer.powerups:
        old = old_powerups.get(new.pid)
        if old is None or new.pid == 0:
            powerups.append(new)
        else:
            powerups.append(PowerUp(x=_lerp(old.x, new.x, t), y=new.y,
                                    kind=new.kind, pid=new.pid))

    return GameState(
        tick=newer.tick,
        players=players,
        obstacles=obstacles,
        powerups=powerups,
        score=newer.score,
        distance=newer.distance,
        speed=newer.speed,
        effects=newer.effects,
        shield=newer.shield,
        running=newer.running,
    )
