"""Advance the world exactly one fixed tick. Pure Python, no Kivy.

Every device runs one of these, and they run the *same* one: same seed, same
tick, therefore the same obstacles in the same places without a byte of them
crossing the network. What differs between devices is only which dino each one
owns.

  local dino   -- fully simulated here. Input applied, gravity integrated,
                  collisions ruled on. This device is the only authority on it
                  and never asks anyone's permission.
  remote dinos -- puppets. ``Player.remote`` is set, forces and integration
                  skip them entirely, and their position is written each frame
                  from their owner's snapshots (net/peers.py). They still take
                  part in the rope, which reads their height; they just cannot
                  be moved by it here.

The consequence worth stating plainly: a device rules on its own dino's
collisions and nobody else's. Death is announced on the reliable channel rather
than discovered, which is what stops two devices disagreeing about whether a
run ended (game/coop.py).
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
        # Power-ups this device has already reported touching. A pickup is not
        # applied where it is noticed -- it changes how fast the world scrolls,
        # so both devices have to start it on the same tick or their obstacle
        # streams drift apart. It is claimed here and applied when the
        # authoritative POWERUP event names a tick (game/coop.py). Without this
        # set, the overlap would be re-claimed 60 times a second while the
        # answer was in flight.
        self.claimed_pids: set[int] = set()

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

    def apply_powerup(self, kind: str, pid: int = 0) -> None:
        """Apply a power-up to the whole team, and take it off the field.

        Called when the pickup has been *agreed*, not when it was noticed --
        every device runs this on the same tick, from the same event, so the
        world stays in step. ``pid`` is the power-up's stable id; 0 means "no
        particular one" (only used by the single-device local mode).
        """
        state = self.state
        if pid:
            state.powerups = [p for p in state.powerups if p.pid != pid]
            self.claimed_pids.discard(pid)
        if kind == C.POWERUP_SHIELD:
            state.shield = True
        elif kind == C.POWERUP_STAR:
            state.bonus += C.STAR_SCORE_BONUS
            state.score += C.STAR_SCORE_BONUS
        else:
            state.effects[kind] = C.POWERUP_DURATIONS[kind]
        state.events.append({"e": "powerup", "kind": kind})

    # -- tick ---------------------------------------------------------------

    def step(self, dt: float) -> list:
        """Advance one fixed tick. Returns power-ups this device just touched.

        Order is load-bearing: all velocity writes (gravity, jump, rope) must
        happen before any position update, so the ground clamp sees the final
        velocity for the tick.
        """
        state = self.state
        if not state.running:
            return []

        state.tick += 1
        state.events.clear()
        self._tick_effects(dt)
        mods = self.modifiers()

        owned = [p for p in state.players if not p.remote]
        grounded_before = [(p, p.grounded) for p in owned]

        # 1. Velocity: gravity + jump. Only for dinos this device owns --
        #    integrating a puppet would fight the position its own device is
        #    sending us, and lose.
        for player in owned:
            physics.apply_player_forces(player, dt, mods)

        # 2. Velocity: rope tension between neighbours. Deliberately before the
        #    ground clamp -- otherwise the clamp would re-pin a grounded player
        #    every tick and a partner's tug could never lift them.
        #
        #    The rope is the one place a puppet still matters: it reads their
        #    height and pulls us accordingly. It also writes an equal-and-
        #    opposite reaction onto them, which is Newton but not ours to
        #    apply -- their device is computing that same reaction against its
        #    own view of us. So it is taken back immediately below. Letting it
        #    stand would have the puppet drift away from the position its owner
        #    is reporting, between packets, in a direction nobody asked for.
        remote_vy = [(p, p.vy) for p in state.players if p.remote]
        physics.apply_rope_forces(state.players, dt, mods)
        for player, vy in remote_vy:
            player.vy = vy

        # 3. Position + ground clamp, again only for what we own.
        for player in owned:
            physics.integrate_and_clamp(player, dt)

        # Jump / land events, for sound and dust. Remote dinos raise theirs
        # from their snapshot flags instead (game/coop.py), so a partner's
        # landing thud is not synthesised from a position we did not compute.
        for player, was_grounded in grounded_before:
            if was_grounded and not player.grounded:
                state.events.append({"e": "jump", "player": player.id})
            elif not was_grounded and player.grounded:
                state.events.append({"e": "land", "player": player.id})

        # 4. Scroll the world, spawn, accrue distance/score. Pure function of
        #    (seed, tick, effects) -- which is why every device gets the same
        #    obstacles from the seed alone.
        self.world.update(state, dt, mods.speed_scale, mods.gravity_scale)

        # 5. Pickups (claimed, not applied), then this device's own collisions.
        claimed = self._claim_powerups(owned)
        self._check_collisions(owned)
        return claimed

    def _claim_powerups(self, owned: list[Player]) -> list:
        """Power-ups one of our own dinos is touching, first time only.

        Nothing is applied and nothing is removed here. A power-up that bends
        gravity or slows the world changes where the next obstacle lands, so it
        has to start on a tick both devices agree on -- see apply_powerup.
        """
        claimed = []
        for powerup in self.state.powerups:
            if powerup.pid in self.claimed_pids:
                continue
            box = powerup.hitbox()
            if any(p.alive and p.hitbox().overlaps(box) for p in owned):
                self.claimed_pids.add(powerup.pid)
                claimed.append(powerup)
        return claimed

    def _check_collisions(self, owned: list[Player]) -> None:
        """Rule on our own dinos hitting things. Never on anyone else's.

        A remote dino's collisions are its own device's business, and it will
        say so over the reliable channel. Testing them here would mean killing
        a partner based on a position that is INTERP_DELAY out of date -- a
        death they did not experience and cannot argue with.
        """
        state = self.state
        for player in owned:
            if not player.alive:
                continue
            box = player.hitbox()
            for obstacle in state.obstacles:
                if not box.overlaps(obstacle.hitbox()):
                    continue

                if state.shield:
                    # The shield eats one fatal hit for the whole team. Remove
                    # the obstacle too, or it would kill them again next tick.
                    self.break_shield(player.id, obstacle)
                    return

                player.alive = False
                state.running = False
                state.cause_player_id = player.id
                state.cause_obstacle = obstacle.kind
                state.events.append({"e": "crash", "player": player.id,
                                     "obstacle": obstacle.kind})
                return

    # -- events applied from the reliable channel ---------------------------

    def break_shield(self, player_id: int, obstacle=None) -> None:
        """Spend the team's shield on one hit, and clear what caused it."""
        state = self.state
        state.shield = False
        if obstacle is not None and obstacle in state.obstacles:
            state.obstacles.remove(obstacle)
        state.events.append({"e": "shield_break", "player": player_id})

    def kill(self, player_id: int, obstacle: str | None) -> None:
        """End the run because a player died -- ours or, told to us, theirs."""
        state = self.state
        for player in state.players:
            if player.id == player_id:
                player.alive = False
                break
        if not state.running:
            return  # already over; the first death is the one that counts
        state.running = False
        state.cause_player_id = player_id
        state.cause_obstacle = obstacle
        state.events.append({"e": "crash", "player": player_id,
                             "obstacle": obstacle})

    def revive(self, player_id: int) -> None:
        """Put a dead player back on their feet and resume the run.

        Nothing in the game triggers this yet -- a crash ends the run for
        everyone. It exists because REVIVE is part of the reliable event
        contract (see NETCODE.md): the channel, the handler and the state
        change are all here and working, so adding a mechanic that uses it is
        a UI change rather than a netcode change.
        """
        state = self.state
        for player in state.players:
            if player.id != player_id:
                continue
            player.alive = True
            player.vy = 0.0
            if state.cause_player_id == player_id:
                state.running = True
                state.cause_player_id = None
                state.cause_obstacle = None
            state.events.append({"e": "revive", "player": player_id})
            return
