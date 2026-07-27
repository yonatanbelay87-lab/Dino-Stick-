"""Client-side prediction for the one dino this device controls.

The problem it solves: interpolation renders the world at ``now -
INTERP_DELAY``, and input has to reach the host and come back. Applied to your
own dino that means pressing jump and watching it happen a round trip later,
which is the single most noticeable flaw in a networked action game -- far
worse than seeing a partner 100 ms behind, because you feel your own input.

The fix is to stop waiting: simulate your own dino locally the instant the
input is read, and treat the host's later word as a correction rather than as
news.

What is NOT predicted is as important as what is:

  * **The rope is left out entirely.** It couples you to neighbours whose
    inputs this device does not have. Predicting it would mean inventing their
    jumps -- confident, wrong motion that has to be yanked back a moment
    later, which is worse than no prediction at all. So every rope effect
    (including being ripped off the floor by a partner) arrives as a
    correction from the host, exactly like it does today.
  * **Nothing else.** Obstacles, power-ups, score and every other player stay
    authoritative and interpolated. This predicts one dino's vertical motion.

Accuracy comes from reusing the host's own functions rather than
reimplementing them: ``apply_player_forces`` then ``integrate_and_clamp``, the
same two calls the simulation makes, with the rope step between them simply
skipped. A reimplementation would drift the moment either is touched.
"""

from __future__ import annotations

from . import constants as C
from . import physics
from .entities import Modifiers, Player

_NO_MODS = Modifiers()


class LocalPredictor:
    """Runs the local dino ahead of the network, then eases it back on course."""

    def __init__(self) -> None:
        self.player: Player | None = None
        # Leftover real time, spent in whole TICK_DT slices.
        self._accumulator = 0.0
        # Diagnostics: how far prediction was off when last corrected, and
        # whether that correction had to be a hard snap.
        self.last_error = 0.0
        self.snapped = False

    @property
    def active(self) -> bool:
        return self.player is not None

    def begin(self, authoritative: Player) -> None:
        """(Re)seed prediction from an authoritative player state."""
        self.player = Player(
            id=authoritative.id,
            name=authoritative.name,
            skin=authoritative.skin,
            x=authoritative.x,
            y=authoritative.y,
            vy=authoritative.vy,
            grounded=authoritative.grounded,
            ducking=authoritative.ducking,
            alive=authoritative.alive,
        )
        self._accumulator = 0.0
        self.last_error = 0.0
        self.snapped = False

    def stop(self) -> None:
        self.player = None
        self._accumulator = 0.0

    # -- simulation ---------------------------------------------------------

    def set_input(self, jump: bool, duck: bool) -> None:
        if self.player is not None:
            self.player.want_jump = jump
            self.player.want_duck = duck

    def step(self, frame_dt: float, mods: Modifiers | None = None) -> None:
        """Advance the prediction, in the same fixed slices the host uses.

        The step size matters as much as the equations. Gravity is integrated
        with plain Euler, whose result depends on dt, so stepping by a variable
        frame time would trace a subtly different arc from the host's fixed
        60 Hz -- a systematic drift that reconciliation would then fight every
        single frame. Banking real time and spending it in whole TICK_DT
        slices reproduces the host's arithmetic exactly.
        """
        player = self.player
        if player is None or not player.alive:
            return

        self._accumulator += min(frame_dt, C.MAX_FRAME_DT)
        ticks = 0
        while (self._accumulator >= C.TICK_DT
               and ticks < C.MAX_TICKS_PER_FRAME):
            physics.apply_player_forces(player, C.TICK_DT, mods or _NO_MODS)
            # No apply_rope_forces here. That gap is the whole design.
            physics.integrate_and_clamp(player, C.TICK_DT)
            self._accumulator -= C.TICK_DT
            ticks += 1
        if ticks == C.MAX_TICKS_PER_FRAME:
            self._accumulator = 0.0

    # -- reconciliation -----------------------------------------------------

    def _catch_up(self, authoritative: Player, age: float,
                  mods: Modifiers | None) -> Player:
        """Where the host's sample would be by now, had nothing surprised it.

        Snapshots describe the past: by the time one is decoded it is already
        a packet's flight plus up to a snapshot interval old. Reconciling
        against that raw value drags the prediction backwards toward a stale
        position every single frame -- measured at 44 px behind the truth at
        the peak of a jump, most of a dino's height, which is enough to make
        you look like you cleared a cactus you actually hit.

        Rolling the sample forward by its own age, with the same integrator,
        removes that systematic bias and leaves reconciliation doing only what
        it should: correcting things prediction genuinely could not know, like
        the rope.
        """
        ahead = Player(id=authoritative.id, y=authoritative.y,
                       vy=authoritative.vy, grounded=authoritative.grounded,
                       ducking=authoritative.ducking,
                       alive=authoritative.alive,
                       airborne_time=authoritative.airborne_time)
        # want_jump stays False: this is coasting the sample forward, not
        # replaying input, and a phantom jump here would be far worse than the
        # staleness it is correcting.
        remaining = max(0.0, min(age, C.MAX_FRAME_DT))
        while remaining >= C.TICK_DT:
            physics.apply_player_forces(ahead, C.TICK_DT, mods or _NO_MODS)
            physics.integrate_and_clamp(ahead, C.TICK_DT)
            remaining -= C.TICK_DT
        return ahead

    def reconcile(self, authoritative: Player, age: float = 0.0,
                  mods: Modifiers | None = None) -> float:
        """Ease the prediction toward the host's version. Returns the error.

        Small errors are blended away over several frames; large ones are
        applied outright. The threshold is what separates "prediction was
        slightly off" from "something happened you could not have known
        about", and only the second deserves a visible jump.

        ``age`` is how old the authoritative sample is; see ``_catch_up``.
        """
        player = self.player
        if player is None:
            return 0.0

        if age > 0.0:
            authoritative = self._catch_up(authoritative, age, mods)

        error = authoritative.y - player.y
        self.last_error = error
        self.snapped = abs(error) > C.RECONCILE_SNAP

        if self.snapped:
            player.y = authoritative.y
            player.vy = authoritative.vy
            player.grounded = authoritative.grounded
            player.airborne_time = 0.0 if authoritative.grounded else \
                C.COYOTE_TIME + 1.0
        else:
            player.y += error * C.RECONCILE_LERP
            player.vy += (authoritative.vy - player.vy) * C.RECONCILE_LERP
            # Landing and dying are state, not position: taking them straight
            # from the host stops a predicted dino from hovering a few pixels
            # up, still "airborne", after the host has already landed it.
            if authoritative.grounded and abs(error) < 1.0:
                player.y = authoritative.y
                player.grounded = True

        player.alive = authoritative.alive
        return error

    # -- output -------------------------------------------------------------

    def apply_to(self, state) -> None:
        """Replace the local dino in a rendered state with the predicted one.

        Only the fields prediction owns are copied. Name, skin and x belong to
        the roster and the slot, not to the physics.
        """
        player = self.player
        if player is None:
            return
        for target in state.players:
            if target.id != player.id:
                continue
            target.y = player.y
            target.vy = player.vy
            target.grounded = player.grounded
            target.ducking = player.ducking
            return
