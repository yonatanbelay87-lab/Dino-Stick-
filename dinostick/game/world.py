"""Seeded obstacle / power-up spawning and the difficulty ramp.

Every spawn decision comes from one seeded ``random.Random``, drawn in a fixed
order, so a host and any client running the same seed produce an identical
world. Never call the module-level ``random`` here.
"""

from __future__ import annotations

import math
import random

from . import constants as C
from .entities import GameState, Obstacle, PowerUp


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class World:
    """Owns the spawn RNG and the difficulty curve for a single run."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        # Seconds until the next obstacle enters at SPAWN_X.
        self.spawn_timer: float = C.FIRST_SPAWN_DELAY
        # Monotonic obstacle ids, so clients can match them between snapshots.
        self._next_oid: int = 1
        self._next_pid: int = 1
        self.powerup_timer: float = C.POWERUP_FIRST_DELAY

    # -- difficulty ---------------------------------------------------------

    def speed_for_distance(self, distance: float) -> float:
        """Scroll speed ramps with the square root of distance, then caps."""
        speed = C.BASE_SPEED + C.SPEED_K * math.sqrt(max(0.0, distance))
        return min(speed, C.MAX_SPEED)

    def difficulty(self, speed: float) -> float:
        """0.0 at BASE_SPEED, 1.0 at MAX_SPEED."""
        span = C.MAX_SPEED - C.BASE_SPEED
        if span <= 0.0:
            return 1.0
        return max(0.0, min(1.0, (speed - C.BASE_SPEED) / span))

    # -- spawning -----------------------------------------------------------

    def _next_interval(self, raw_speed: float, current_speed: float,
                       spread: float = 0.0, gravity_scale: float = 1.0,
                       obstacle_width: float = 0.0) -> float:
        """Seconds until the next spawn.

        The gap is sized as a multiple of one jump arc rather than as a raw
        distance. That is the whole trick: since both the gap and the jump
        scale with speed, a multiple above 1.0 stays landable at *any* scroll
        speed, while the multiple itself shrinks with difficulty -- so the
        interval tightens without ever becoming physically impossible.

        Crucially the gap is computed in PIXELS against ``raw_speed`` -- the
        unslowed speed -- and only then converted to seconds at whatever the
        world is currently scrolling at. Doing it directly in seconds is a trap:
        while Slow-mo is active a perfectly correct *time* gap lays obstacles
        down closer together *in space*, and once the effect expires they come
        at you at full speed packed tighter than one jump arc. That bug killed
        a bot 2 seconds after a Slow-mo wore off, with nothing on screen to
        explain why.

        ``spread`` is the distance between the first and last dino. A roped
        team is not a point: the trailing dino meets every obstacle later, so
        without that term a 4-player chain could not clear tight gaps no matter
        how well it played.
        """
        t = self.difficulty(raw_speed)
        lo = _lerp(C.GAP_MIN_EASY, C.GAP_MIN_HARD, t)
        hi = _lerp(C.GAP_MAX_EASY, C.GAP_MAX_HARD, t)
        # Hang time is inversely proportional to gravity, so while Feather is
        # active one jump takes longer and the gaps have to grow with it.
        arc = C.JUMP_AIR_TIME / max(0.1, gravity_scale)

        # Distance the team needs: one arc of travel, plus its own width, plus
        # the width of the obstacle being cleared (spacing is measured
        # leading-edge to leading-edge, so a wide obstacle eats into the gap).
        gap_px = arc * self.rng.uniform(lo, hi) * raw_speed
        gap_px += spread + obstacle_width

        return gap_px / max(current_speed, 1.0)

    def _pick_kind(self, speed: float) -> str:
        """Weighted pick over the obstacle table, biased harder with difficulty.

        Each type has an easy weight, a hard weight and an unlock point; types
        below their unlock point cannot appear at all, so the first minute is
        cacti and rocks and the birds arrive once you have found your rhythm.
        """
        t = self.difficulty(speed)
        kinds: list[str] = []
        weights: list[float] = []
        for kind, (easy, hard, unlock) in C.OBSTACLE_WEIGHTS.items():
            if t < unlock:
                continue
            weight = _lerp(easy, hard, t)
            if weight > 0.0:
                kinds.append(kind)
                weights.append(weight)
        if not kinds:  # defensive: table misconfigured
            return "CACTUS_SMALL"

        roll = self.rng.random() * sum(weights)
        for kind, weight in zip(kinds, weights):
            roll -= weight
            if roll <= 0.0:
                return kind
        return kinds[-1]

    def _pick_powerup(self) -> str:
        kinds = list(C.POWERUP_WEIGHTS)
        weights = [C.POWERUP_WEIGHTS[k] for k in kinds]
        roll = self.rng.random() * sum(weights)
        for kind, weight in zip(kinds, weights):
            roll -= weight
            if roll <= 0.0:
                return kind
        return kinds[-1]

    # -- per-tick -----------------------------------------------------------

    def update(self, state: GameState, dt: float, speed_scale: float = 1.0,
               gravity_scale: float = 1.0) -> None:
        """Scroll, cull and spawn; advance distance/score/speed.

        ``speed_scale`` is the Slow-mo multiplier. Note it scales how fast the
        world moves but NOT the difficulty curve, which stays tied to distance
        travelled -- so slow-mo buys reaction time without rewinding progress.
        """
        # raw_speed drives difficulty and spacing; state.speed is what the
        # world actually scrolls at. Keeping them apart is what stops Slow-mo
        # from also rewinding the difficulty ramp.
        raw_speed = self.speed_for_distance(state.distance)
        state.speed = raw_speed * speed_scale
        dx = state.speed * dt
        state.distance += dx
        state.score = int(state.distance * C.SCORE_PER_PIXEL) + state.bonus

        # The players hold a fixed x; the world moves past them instead.
        for obstacle in state.obstacles:
            obstacle.x -= dx
        state.obstacles = [o for o in state.obstacles if o.x > C.DESPAWN_X]
        for powerup in state.powerups:
            powerup.x -= dx
        state.powerups = [p for p in state.powerups if p.x > C.DESPAWN_X]

        self.spawn_timer -= dt
        if self.spawn_timer <= 0.0:
            kind = self._pick_kind(raw_speed)
            state.obstacles.append(
                Obstacle(x=C.SPAWN_X, y=C.OBSTACLE_Y[kind], kind=kind,
                         oid=self._next_oid)
            )
            self._next_oid += 1
            xs = [p.x for p in state.players]
            spread = (max(xs) - min(xs)) if xs else 0.0
            self.spawn_timer += self._next_interval(
                raw_speed, state.speed, spread, gravity_scale,
                C.OBSTACLE_SIZES[kind][0])

        self.powerup_timer -= dt
        if self.powerup_timer <= 0.0:
            state.powerups.append(
                PowerUp(x=C.SPAWN_X,
                        y=self.rng.choice(C.POWERUP_HEIGHTS),
                        kind=self._pick_powerup(),
                        pid=self._next_pid)
            )
            self._next_pid += 1
            self.powerup_timer += self.rng.uniform(C.POWERUP_INTERVAL_MIN,
                                                   C.POWERUP_INTERVAL_MAX)
