"""A networked run, from this device's point of view.

One of these exists per device during a co-op run, and the host's is not
special: it has the same simulation, the same loop and the same authority. What
each one owns is its own dino and nothing else.

The three rules the rest of the file is built from:

  1. **Local input never touches the network.** Jump is applied to the local
     dino on the frame the finger went down. Nothing is sent first, nothing is
     waited for, and no later packet is allowed to overrule it. This is the
     entire fix for the joiner's input lag -- there is no round trip left in
     the loop to be slow.

  2. **Partners are drawn from their own snapshots, INTERP_DELAY in the past.**
     Never simulated here, never predicted, never corrected. Their device is
     the only authority on where they are, and it says so 20 times a second.

  3. **The world is the seed.** Obstacles, power-ups, the difficulty ramp: all
     a pure function of (seed, tick), computed identically on both devices. No
     obstacle has ever been on the wire and none needs to be. What that buys is
     not bandwidth -- it is that a joiner's world is not a *copy* of the host's
     arriving late, it is the same world, computed locally, now.

The only thing the host still arbitrates is the handful of events that change
the shared world: a power-up pickup, a shield spent, a death. Those go through
the reliable channel and are applied on an agreed tick, because two devices
applying them at different moments is precisely how rule 3 breaks.

There is no reconciliation here and no lag compensation. Both are answers to
"the server disagrees with you", and in this design there is no server to
disagree.

The rope is the one honest compromise, and it is worth stating rather than
burying. It couples this dino's velocity to a partner's height -- but the only
partner height available here is the one being rendered, INTERP_DELAY in the
past. So a tug is felt slightly after it was given. Drawing the rope from the
same delayed position is what keeps that consistent: what you see pulling you
is what pulls you. See NETCODE.md.
"""

from __future__ import annotations

from typing import Any, Callable

from net import protocol
from net.peers import EXTRAP, LIVE, STALE, PeerTable
from net.statepacket import flags_for, pack_state

from . import constants as C
from . import timing
from .entities import Player
from .simulation import Simulation

# Transport hooks. The session does not know or care whether it is the host
# fanning packets out to three clients or a joiner sending to one host.
SendState = Callable[[bytes], None]
SendReliable = Callable[[dict[str, Any]], None]


class CoopSession:
    """This device's half of a networked run."""

    def __init__(self, seed: int, players: list[Player], local_id: int,
                 send_state: SendState, send_reliable: SendReliable,
                 is_host: bool, countdown: float = 0.0) -> None:
        for player in players:
            player.remote = player.id != local_id

        self.sim = Simulation(seed, players)
        self.local_id = local_id
        self.is_host = is_host
        self.peers = PeerTable()

        self._send_state = send_state
        self._send_reliable = send_reliable

        self._seq = 0
        self._accumulator = 0.0
        # Held down until the countdown expires, so both devices reach world
        # tick 0 together instead of whenever their START happened to land.
        self._countdown = max(0.0, countdown)

        # Everything that happened this rendered frame, for sound and
        # particles: the events of every tick stepped, plus the partners'.
        # Accumulated rather than read off state.events, which the simulation
        # clears at the top of each tick -- a frame that steps twice would
        # otherwise lose the first tick's jump.
        self.frame_events: list[dict] = []

        # Power-ups granted but not yet due: (apply_tick, kind, pid).
        self._scheduled: list[tuple[int, str, int]] = []
        # Power-up ids the host has already ruled on, so a second claim for the
        # same pickup (two dinos touching it within one round trip) grants once.
        self._granted: set[int] = set()

        self._last_score_sync = 0.0
        # Ticks this device's world clock is behind the host's, smoothed. Only
        # a joiner ever moves it; the host is the reference by definition.
        self._tick_error = 0.0
        # One-way flight time in seconds, from the client's measured RTT. Used
        # only to age-correct the tick comparison above.
        self._flight = 0.0
        self._ended = False

    # -- lifecycle ----------------------------------------------------------

    @property
    def state(self):
        return self.sim.state

    @property
    def running(self) -> bool:
        return self.sim.state.running

    @property
    def armed(self) -> bool:
        """False while the start countdown is still running."""
        return self._countdown <= 0.0

    def local_player(self) -> Player | None:
        for player in self.sim.state.players:
            if player.id == self.local_id:
                return player
        return None

    def peer_ids(self) -> list[int]:
        return [p.id for p in self.sim.state.players if p.remote]

    def set_flight_time(self, one_way: float) -> None:
        """Tell the session how long a packet takes to arrive, in seconds.

        Fed from the client's measured RTT. Affects only the world-clock
        comparison; nothing about rendering or input depends on it, and a wrong
        value costs a tick of obstacle alignment, not a frame of input lag.
        """
        self._flight = max(0.0, one_way)

    # -- input: the part with no network in it ------------------------------

    def set_local_input(self, jump: bool, duck: bool) -> None:
        """Apply this device's controls to this device's dino. Immediately.

        Called from the render frame, before the simulation steps, so a press
        read this frame is airborne this frame. There is deliberately nothing
        else in this method: no send, no sequence number, no acknowledgement.
        The input has already had its effect by the time anyone else hears
        about it, and what they hear about is where the dino ended up.
        """
        player = self.local_player()
        if player is not None:
            player.want_jump = jump
            player.want_duck = duck

    # -- the render frame ---------------------------------------------------

    def advance(self, frame_dt: float):
        """Step the world by one rendered frame and return what to draw.

        Runs on the Kivy thread at display rate. The fixed-timestep accumulator
        is the same one single-player uses, and for the same reason: gravity is
        integrated with plain Euler and the rope is a spring, so a variable dt
        would trace a different arc on every device and on every phone. Real
        elapsed time is banked and spent in whole TICK_DT slices.
        """
        state = self.sim.state
        self.frame_events = []

        # Partners first: the rope reads their height during the step below, so
        # writing them afterwards would tug this dino toward where its partner
        # was a frame ago, every frame.
        self._apply_peers()

        if self._countdown > 0.0:
            self._countdown -= frame_dt
            return state

        self._accumulator += min(frame_dt, C.MAX_FRAME_DT)
        budget = self._tick_budget()
        ticks = 0
        while self._accumulator >= C.TICK_DT and ticks < budget:
            self._due_powerups(state.tick + 1)
            claimed = self.sim.step(C.TICK_DT)
            self.frame_events.extend(state.events)
            self._note_own_shield(state.events)
            if claimed:
                self._claim(claimed)
            self._accumulator -= C.TICK_DT
            ticks += 1
            if not state.running:
                break
        if ticks >= C.MAX_TICKS_PER_FRAME:
            # Hit the catch-up ceiling: the app was suspended or the window
            # dragged. Drop the backlog rather than spiralling further behind
            # every frame.
            self._accumulator = 0.0

        if not state.running and not self._ended:
            self._announce_death()

        if self.is_host:
            self._maybe_score_sync()
        return state

    def _tick_budget(self) -> int:
        """Ticks allowed this frame, nudged by one to close world-clock drift.

        Two devices free-running their own 60 Hz accumulators drift apart by a
        tick every few seconds, and a tick of drift is ~10 px of obstacle
        offset between the two screens. Rather than resynchronise with a jump,
        the joiner runs one tick more (or one fewer) per frame until the gap is
        gone: at 60 fps that closes 20 ticks in a third of a second, and no
        single frame moves by more than it already would.
        """
        budget = C.MAX_TICKS_PER_FRAME
        if self.is_host or abs(self._tick_error) <= C.WORLD_SYNC_DEADBAND:
            return budget
        if self._tick_error > 0.0:
            # Behind: bank one extra tick so this frame steps once more than
            # the wall clock earned it.
            self._accumulator += C.TICK_DT
            self._tick_error -= 1.0
            return budget + 1
        # Ahead: spend a tick's worth of banked time without stepping.
        self._accumulator = max(0.0, self._accumulator - C.TICK_DT)
        self._tick_error += 1.0
        return budget

    def _apply_peers(self) -> None:
        """Write every remote dino from its owner's snapshots."""
        now = timing.now()
        for player in self.sim.state.players:
            if not player.remote:
                continue
            snap = self.peers.sample(player.id, now)
            if snap is None:
                # Never heard from them. Leave the dino where the roster put it
                # but slack the rope: an anchor we have no evidence for is
                # still an anchor.
                player.connected = False
                continue
            was_grounded = player.grounded
            player.y = snap.y
            player.vy = snap.vy
            player.grounded = snap.grounded
            player.ducking = snap.ducking
            player.alive = snap.alive
            player.connected = (
                self.peers.silent_for(player.id, now) < C.PEER_ROPE_TIMEOUT)
            # A partner's jump and landing come from their flags, not from
            # physics we did not run: sound and dust for a motion we only
            # received.
            if was_grounded and not player.grounded:
                self.frame_events.append({"e": "jump", "player": player.id})
            elif not was_grounded and player.grounded:
                self.frame_events.append({"e": "land", "player": player.id})

    def peer_quality(self) -> str:
        """The worst any partner is currently being drawn at, for the HUD."""
        worst = LIVE
        for player in self.sim.state.players:
            if not player.remote:
                continue
            quality = self.peers.quality(player.id)
            if quality == STALE:
                return STALE
            if quality == EXTRAP:
                worst = EXTRAP
        return worst

    # -- the 20 Hz network tick ---------------------------------------------

    def net_tick(self, inbound=None) -> None:
        """Send our dino, drain what has arrived. Called at NET_TICK_HZ.

        Deliberately not the render loop. Sending at 60 Hz is three times the
        packets for motion the receiver reconstructs by interpolation anyway,
        and draining on the render loop turns a burst of queued packets into a
        dropped frame. Separating them lets the two rates be tuned against
        completely different things -- one against the eye, one against the
        network.
        """
        if inbound is not None:
            self._drain(inbound)
        if self.armed:
            self._broadcast_local()

    def _drain(self, inbound) -> None:
        """Pop everything the receive thread has queued, oldest first.

        The queue is filled by a daemon socket thread and emptied here, on the
        Kivy thread. That is the whole of the threading contract: one thread
        parses bytes and appends, one thread pops and touches the game. No
        locks, because ``deque.popleft`` is atomic under the GIL and neither
        side ever reads what the other is writing.
        """
        while True:
            try:
                payload, received_at = inbound.popleft()
            except IndexError:
                break
            snap = self.peers.ingest(payload, received_at,
                                     ignore_id=self.local_id)
            if snap is not None and not self.is_host:
                self._note_peer_tick(snap.player_id, snap.tick)

    def _note_peer_tick(self, player_id: int, peer_tick: int) -> None:
        """Learn how far this device's world clock is from the host's."""
        if player_id != C.HOST_PLAYER_ID:
            return
        # The snapshot describes the host's world one flight-time ago, so the
        # honest comparison adds that back on. Without it the joiner converges
        # on being permanently half a round trip behind -- steering its clock
        # toward a number that was already stale when it arrived.
        aged = peer_tick + int(self._flight / C.TICK_DT)
        error = aged - self.sim.state.tick
        if abs(error) > C.WORLD_SYNC_SNAP:
            # A stall, a suspend, or a device that just woke up. Easing a gap
            # this size would mean seconds of visibly wrong obstacle spacing.
            self.sim.state.tick = aged
            self._tick_error = 0.0
            return
        self._tick_error = self._tick_error * 0.8 + error * 0.2

    def _broadcast_local(self) -> None:
        """15 bytes: where our dino is and what it is doing."""
        player = self.local_player()
        if player is None:
            return
        self._seq = (self._seq + 1) & 0xFFFF
        self._send_state(pack_state(
            player_id=player.id,
            seq=self._seq,
            tick=self.sim.state.tick,
            x=player.x,
            y=player.y,
            vy=player.vy,
            flags=flags_for(player.want_jump, player.want_duck, player.alive,
                            player.grounded, player.ducking),
        ))

    # -- shared world events, over the reliable channel ---------------------

    def _claim(self, powerups: list) -> None:
        """Report touching a power-up. Does not apply it -- see MSG_POWERUP."""
        for powerup in powerups:
            if self.is_host:
                self._grant(powerup.pid, powerup.kind)
            else:
                self._send_reliable(protocol.powerup_claim(
                    powerup.pid, powerup.kind, self.sim.state.tick))

    def _grant(self, pid: int, kind: str) -> None:
        """Host only: rule on a claim and tell everyone when it takes effect."""
        if pid in self._granted:
            return  # already ruled on; a second claim was in flight
        self._granted.add(pid)
        apply_tick = self.sim.state.tick + C.WORLD_EVENT_LEAD
        self._send_reliable(protocol.powerup(pid, kind, apply_tick))
        self._scheduled.append((apply_tick, kind, pid))

    def _due_powerups(self, tick: int) -> None:
        """Apply anything whose agreed tick has arrived."""
        if not self._scheduled:
            return
        due = [item for item in self._scheduled if item[0] <= tick]
        if not due:
            return
        self._scheduled = [item for item in self._scheduled if item[0] > tick]
        for apply_tick, kind, pid in due:
            self._apply_effect(kind, pid, tick - apply_tick)

    def _apply_effect(self, kind: str, pid: int, late_ticks: int) -> None:
        """Start an effect, correcting for having been told about it late.

        Slow-mo and Feather change how far the world scrolls per tick, so a
        device that applies one N ticks after the agreed moment has scrolled N
        ticks too far. Distance drives the difficulty ramp and the spawn
        spacing, so left alone that is a permanent divergence in where
        obstacles land -- small, but it never heals and it compounds with every
        pickup.

        The correction is first-order: it assumes speed held constant across
        those N ticks, which for N of a handful is true to well under a pixel.
        ``state.speed`` is still the *pre-effect* speed here, because only
        World.update recomputes it, and that runs next tick.
        """
        state = self.sim.state
        if late_ticks <= 0:
            self.sim.apply_powerup(kind, pid)
            return
        before = self.sim.modifiers().speed_scale
        self.sim.apply_powerup(kind, pid)
        after = self.sim.modifiers().speed_scale
        if before > 0.0 and before != after:
            overscroll = (late_ticks * C.TICK_DT * state.speed
                          * (before - after) / before)
            state.distance -= overscroll

    def _note_own_shield(self, events: list[dict]) -> None:
        """If our hit spent the team shield, say so before it matters."""
        for event in events:
            if (event.get("e") == "shield_break"
                    and event.get("player") == self.local_id):
                self._send_reliable(protocol.shield_break(
                    self.local_id, self.sim.state.tick))

    def _announce_death(self) -> None:
        """Our dino died. Say so once, reliably, and stop the local run."""
        self._ended = True
        state = self.sim.state
        if state.cause_player_id != self.local_id:
            return  # ended for some other reason; nothing of ours to report
        self._send_reliable(protocol.death(
            self.local_id, state.tick, state.cause_obstacle))

    def _maybe_score_sync(self) -> None:
        now = timing.now()
        if now - self._last_score_sync < C.SCORE_SYNC_INTERVAL:
            return
        self._last_score_sync = now
        state = self.sim.state
        self._send_reliable(protocol.score_sync(
            state.tick, state.score, state.distance, state.bonus))

    # -- inbound reliable events --------------------------------------------

    def on_reliable(self, msg: dict[str, Any]) -> bool:
        """Handle one reliable message. True if the run just ended.

        Runs on the Kivy thread, from the app's single network pump.
        """
        kind = msg.get(protocol.TYPE)

        if kind == protocol.MSG_POWERUP_CLAIM:
            # Host only: someone touched a power-up and is asking when it
            # counts. Nobody else is ever sent this message.
            if self.is_host:
                self._grant(int(msg.get("pid", 0)), str(msg.get("kind", "")))
            return False

        if kind == protocol.MSG_POWERUP:
            pid = int(msg.get("pid", 0))
            apply_tick = int(msg.get("apply_tick", 0))
            self._granted.add(pid)
            self.sim.claimed_pids.add(pid)
            if apply_tick <= self.sim.state.tick:
                # Already late: apply now and take back the scroll that should
                # not have happened. Only on a link slower than
                # WORLD_EVENT_LEAD.
                self._apply_effect(str(msg.get("kind", "")), pid,
                                   self.sim.state.tick - apply_tick)
            else:
                self._scheduled.append((apply_tick, str(msg.get("kind", "")),
                                        pid))
            return False

        if kind == protocol.MSG_SHIELD:
            if int(msg.get("id", 0)) == self.local_id:
                return False  # our own announcement, relayed back to us
            # Two devices can each spend the shield on their own hit inside one
            # round trip, and both survive. That is a rare, harmless piece of
            # generosity -- the alternative is asking the host for permission
            # not to die, which is a round trip in the one place it is least
            # affordable.
            self.sim.break_shield(int(msg.get("id", 0)))
            return False

        if kind == protocol.MSG_DEATH:
            player_id = int(msg.get("id", 0))
            if player_id == self.local_id:
                return False  # our own announcement, relayed back to us
            self.sim.kill(player_id, msg.get("obstacle"))
            self.frame_events.extend(self.sim.state.events)
            self._ended = True
            return True

        if kind == protocol.MSG_REVIVE:
            self.sim.revive(int(msg.get("id", 0)))
            self.frame_events.extend(self.sim.state.events)
            self._ended = False
            return False

        if kind == protocol.MSG_SCORE_SYNC:
            self._on_score_sync(msg)
            return False

        return False

    def _on_score_sync(self, msg: dict[str, Any]) -> None:
        """Take the host's word on the shared numbers, if we have drifted.

        Both devices compute score from distance and distance from the tick, so
        in the normal case this changes nothing at all -- it is a check, not a
        channel. It earns its place when a device has been suspended: the tick
        sync puts the clock back and this puts the difficulty ramp back with it.

        Note it does NOT move obstacles. Their x is their own, accumulated from
        the same dx both devices applied; distance only decides how fast the
        next one comes.
        """
        if self.is_host:
            return
        state = self.sim.state
        distance = float(msg.get("distance", state.distance))
        if abs(distance - state.distance) > C.SCORE_SYNC_TOLERANCE:
            state.distance = distance
        state.bonus = int(msg.get("bonus", state.bonus))
        state.score = int(msg.get("score", state.score))
