"""Host-authoritative simulation: advance the world exactly one fixed tick.

This is the single source of truth. The host runs it; clients only render the
snapshots it produces. Pure Python -- no Kivy, testable headless.
"""

from __future__ import annotations

from . import constants as C
from . import physics
from .entities import GameState, Modifiers, Player
from .world import World


def modifiers_for(effects: dict[str, float]) -> Modifiers:
    """Collapse active effects into the multipliers physics reads.

    Module-level and pure so the client's prediction can ask the same question
    the host does. Duplicating this rule client-side would mean prediction and
    authority quietly disagreeing about what Feather does to gravity, which
    shows up as the local dino drifting only while a power-up is active --
    about the worst class of bug to track down.
    """
    mods = Modifiers()
    if C.POWERUP_SLOWMO in effects:
        mods.speed_scale = C.SLOWMO_SPEED_SCALE
    if C.POWERUP_FEATHER in effects:
        mods.gravity_scale = C.FEATHER_GRAVITY_SCALE
        mods.rope_scale = C.FEATHER_ROPE_SCALE
    if C.POWERUP_SYNC in effects:
        # Sync wins outright: the rope goes completely slack.
        mods.rope_scale = 0.0
    return mods


def make_players(count: int, names: list[str] | None = None,
                 skins: list[int] | None = None) -> list[Player]:
    """Build a roster laid out left->right at evenly spaced fixed x offsets."""
    players: list[Player] = []
    for i in range(count):
        players.append(
            Player(
                id=i,
                name=(names[i] if names and i < len(names) else f"P{i + 1}"),
                skin=(skins[i] if skins and i < len(skins) else i),
                x=C.PLAYER_START_X + i * C.PLAYER_SPACING_X,
            )
        )
    return players


class Simulation:
    def __init__(self, seed: int, players: list[Player]) -> None:
        self.world = World(seed)
        self.state = GameState(players=players, running=True)
        self.state.speed = self.world.speed_for_distance(0.0)

    # -- input --------------------------------------------------------------

    def set_input(self, player_id: int, jump: bool, duck: bool) -> None:
        """Latch a player's most recent input; consumed by the next step()."""
        for player in self.state.players:
            if player.id == player_id:
                player.want_jump = jump
                player.want_duck = duck
                return

    # -- tick ---------------------------------------------------------------

    # -- power-up effects ---------------------------------------------------

    def modifiers(self) -> Modifiers:
        """Collapse the active effects into the multipliers physics reads."""
        return modifiers_for(self.state.effects)

    def _tick_effects(self, dt: float) -> None:
        expired = []
        for kind in self.state.effects:
            self.state.effects[kind] -= dt
            if self.state.effects[kind] <= 0.0:
                expired.append(kind)
        for kind in expired:
            del self.state.effects[kind]
            self.state.events.append({"e": "effect_end", "kind": kind})

    def _collect(self, kind: str) -> None:
        """Apply a picked-up power-up to the whole team."""
        state = self.state
        if kind == C.POWERUP_SHIELD:
            state.shield = True
        elif kind == C.POWERUP_STAR:
            state.bonus += C.STAR_SCORE_BONUS
            state.score += C.STAR_SCORE_BONUS
        else:
            state.effects[kind] = C.POWERUP_DURATIONS[kind]
        state.events.append({"e": "powerup", "kind": kind})

    # -- tick ---------------------------------------------------------------

    def step(self, dt: float) -> None:
        """Advance one fixed tick.

        Order is load-bearing: all velocity writes (gravity, jump, rope) must
        happen before any position update, so the ground clamp sees the final
        velocity for the tick.
        """
        state = self.state
        if not state.running:
            return

        state.tick += 1
        state.events.clear()
        self._tick_effects(dt)
        mods = self.modifiers()

        grounded_before = [p.grounded for p in state.players]

        # 1. Velocity: gravity + jump.
        for player in state.players:
            physics.apply_player_forces(player, dt, mods)

        # 2. Velocity: rope tension between neighbours. Deliberately before the
        #    ground clamp -- otherwise the clamp would re-pin a grounded player
        #    every tick and a partner's tug could never lift them.
        physics.apply_rope_forces(state.players, dt, mods)

        # 3. Position + ground clamp.
        for player in state.players:
            physics.integrate_and_clamp(player, dt)

        # Jump / land events, for sound and dust.
        for player, was_grounded in zip(state.players, grounded_before):
            if was_grounded and not player.grounded:
                state.events.append({"e": "jump", "player": player.id})
            elif not was_grounded and player.grounded:
                state.events.append({"e": "land", "player": player.id})

        # 4. Scroll the world, spawn, accrue distance/score.
        self.world.update(state, dt, mods.speed_scale, mods.gravity_scale)

        # 5. Pickups, then shared fate.
        self._check_powerups()
        self._check_collisions()

    def _check_powerups(self) -> None:
        state = self.state
        remaining = []
        for powerup in state.powerups:
            box = powerup.hitbox()
            if any(p.alive and p.hitbox().overlaps(box) for p in state.players):
                self._collect(powerup.kind)
            else:
                remaining.append(powerup)
        state.powerups = remaining

    def _check_collisions(self) -> None:
        state = self.state
        for player in state.players:
            if not player.alive:
                continue
            box = player.hitbox()
            for obstacle in state.obstacles:
                if not box.overlaps(obstacle.hitbox()):
                    continue

                if state.shield:
                    # The shield eats one fatal hit for the whole team. Remove
                    # the obstacle too, or it would kill them again next tick.
                    state.shield = False
                    state.obstacles.remove(obstacle)
                    state.events.append({"e": "shield_break",
                                         "player": player.id})
                    return

                player.alive = False
                state.running = False
                state.cause_player_id = player.id
                state.cause_obstacle = obstacle.kind
                state.events.append({"e": "crash", "player": player.id,
                                     "obstacle": obstacle.kind})
                return
