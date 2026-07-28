"""Player integration and elastic-rope forces.

Integration is deliberately split into two halves:

    apply_player_forces()  -- everything that writes velocity (gravity, jump)
    integrate_and_clamp()  -- position update + ground clamp

The gap between them is where the rope belongs. Phase 2 inserts
``apply_rope_forces()`` there so a partner's tug can lift a *grounded* player
off the floor: the rope has to modify velocity while the ground clamp has not
run yet, otherwise the clamp would pin the player down every tick and the drag
would be invisible. Callers that don't care (Phase 1, single player) can just
use ``integrate_player()``.
"""

from __future__ import annotations

import math

from . import constants as C
from .entities import Modifiers, Player

_NO_MODS = Modifiers()


def apply_player_forces(player: Player, dt: float,
                        mods: Modifiers | None = None) -> None:
    """Write ``player.vy`` for this tick: gravity, then jump if requested."""
    mods = mods or _NO_MODS
    # Gravity. Ducking in mid-air fast-falls -- the only way to cut a jump
    # short once committed.
    falling_fast = player.want_duck and not player.grounded
    gravity = C.GRAVITY * (C.DUCK_FALL_MULTIPLIER if falling_fast else 1.0)
    player.vy += gravity * mods.gravity_scale * dt

    # Jump fires on the rising edge only, and only from the ground or within
    # the coyote-time grace window just after walking off it.
    rising_edge = player.want_jump and not player.prev_jump
    can_jump = player.grounded or player.airborne_time <= C.COYOTE_TIME
    if rising_edge and can_jump:
        player.vy = C.JUMP_VELOCITY
        player.grounded = False
        # Burn the coyote grace so a single press can never yield two jumps.
        player.airborne_time = C.COYOTE_TIME + 1.0

    player.prev_jump = player.want_jump


def integrate_and_clamp(player: Player, dt: float) -> None:
    """Advance position from velocity, then clamp to the ground plane."""
    player.y += player.vy * dt

    if player.y <= 0.0 and player.vy <= 0.0:
        player.y = 0.0
        player.vy = 0.0
        player.grounded = True
        player.airborne_time = 0.0
    else:
        player.grounded = False
        player.airborne_time += dt

    # Ducking shrinks the hitbox, but only with feet on the floor.
    player.ducking = player.want_duck and player.grounded


def integrate_player(player: Player, dt: float) -> None:
    """Both halves back to back -- correct only when there is no rope."""
    apply_player_forces(player, dt)
    integrate_and_clamp(player, dt)


def apply_rope_forces(players: list[Player], dt: float,
                      mods: Modifiers | None = None) -> None:
    """Couple neighbouring dinos with a damped vertical spring.

    The rope is a chain: player 0-1, 1-2, 2-3. For each linked pair we only
    care about the *vertical* separation -- the x offsets are fixed, so height
    difference is the whole story.

        dy  = y[i+1] - y[i]      # positive when the right-hand dino is higher
        dvy = vy[i+1] - vy[i]    # positive when they are pulling apart
        F   = K_ROPE * dy + ROPE_DAMP * dvy

    The K term is Hooke's law: the further apart, the harder the pull. The
    damping term resists *relative* motion, which is what stops the pair
    oscillating forever and what makes a badly-timed jump bleed energy.

    F is then applied equal-and-opposite (Newton's third law): the lower dino
    is tugged up, the higher one is held back. Because this runs before the
    ground clamp, a large enough F gives a grounded player a positive vy and
    literally rips them off the floor -- that lift is the drag mechanic.

    Note this is Gauss-Seidel: for a 3-4 chain, pair (1,2) sees the velocities
    pair (0,1) just wrote. That is more stable than solving all pairs against a
    stale snapshot, and it is deterministic because the order is always fixed --
    which matters, since the host's result is authoritative for everyone.
    """
    mods = mods or _NO_MODS
    # Sync and Feather both work by scaling this down; at 0 the rope is inert
    # and everyone is briefly free to jump on their own.
    k = C.K_ROPE * mods.rope_scale
    damp = C.ROPE_DAMP * mods.rope_scale
    if k <= 0.0 and damp <= 0.0:
        return

    for i in range(len(players) - 1):
        left = players[i]
        right = players[i + 1]

        # A peer whose state stream has gone quiet is a dino frozen at its last
        # known height, and a frozen dino is an infinitely strong anchor: the
        # spring pulls against a partner that can never move toward you, so the
        # rope quietly becomes a tow rope to a lamp post. Going slack instead
        # means losing a player costs the team their help, not their run.
        if not (left.connected and right.connected):
            continue

        dy = right.y - left.y
        dvy = right.vy - left.vy

        force = k * dy + damp * dvy

        # Past MAX_STRETCH the rope is out of give: add a much stiffer term so
        # it reads as elastic-with-a-limit instead of infinitely stretchy.
        excess = abs(dy) - C.MAX_STRETCH
        if excess > 0.0:
            force += math.copysign(C.STRETCH_STIFFNESS * k * excess, dy)

        left.vy += force * dt
        right.vy -= force * dt


def rope_tension(players: list[Player], index: int) -> float:
    """0.0 (slack) to 1.0 (at MAX_STRETCH) for the rope segment after ``index``.

    Rendering and the Phase 4 HUD tension meter read this; it is derived, so it
    never needs to travel over the network.
    """
    if index + 1 >= len(players):
        return 0.0
    left, right = players[index], players[index + 1]
    if not (left.connected and right.connected):
        return 0.0  # slack, per apply_rope_forces -- the meter must agree
    dy = abs(right.y - left.y)
    return min(1.0, dy / C.MAX_STRETCH)
