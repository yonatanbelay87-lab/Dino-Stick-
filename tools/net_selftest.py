"""Headless netcode checks. No Kivy and no window.

    python tools/net_selftest.py            # from the repo root
    python tools/net_selftest.py --verbose

Lives beside make_branding.py in the repo-root tools/, not inside dinostick/:
buildozer's source.dir is the package directory and source.include_exts picks up
every .py under it, so a test file in there would be shipped inside the APK.

Runs two complete CoopSessions against each other in one process, over an
in-memory link that can drop, delay and jitter packets on demand. That is the
whole point: the interesting failures here are timing failures, and a real LAN
is too well behaved to produce them on request.

What each check is actually defending:

  determinism   Both devices spawn the same obstacles from the seed alone. If
                this breaks, two players are dodging different worlds and no
                amount of interpolation will save it.
  packet        15 bytes, and stale/duplicate/wrapped sequence numbers are
                discarded. The wrap case fires 54 minutes into a run at 20 Hz,
                which is exactly late enough to ship without noticing.
  local input   A jump moves the local dino on the frame it was pressed, with
                the network dead. This is the bug the refactor exists to fix,
                so it is asserted rather than eyeballed.
  partner       Under 20% loss and 60 ms of jitter, the partner still moves
                smoothly -- no frame moves it further than a plausible one --
                and the local dino is bit-identical to a run with a perfect
                link.
"""

from __future__ import annotations

import argparse
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The game's imports are flat (game/, net/, ...), so the package directory has
# to be the import root -- same arrangement as tools/make_branding.py.
sys.path.insert(0, os.path.join(ROOT, "dinostick"))

from game import constants as C  # noqa: E402
from game import timing  # noqa: E402
from game.coop import CoopSession  # noqa: E402
from game.entities import Player  # noqa: E402
from game.simulation import Simulation  # noqa: E402
from net import statepacket  # noqa: E402
from net.peers import PeerBuffer  # noqa: E402

FRAME_DT = 1.0 / 60.0
VERBOSE = False


# ---------------------------------------------------------------------------
# A clock we control, so a "250 ms stall" takes no wall time to test
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def install_clock(clock: FakeClock):
    """Point every timing.now() in the game at our clock.

    Both net/peers.py and game/coop.py reach it as ``timing.now()`` -- an
    attribute lookup at call time -- so replacing the attribute is enough and
    no module needs to know it is being tested.
    """
    real = timing.now
    timing.now = clock.now
    return real


# ---------------------------------------------------------------------------
# An in-process link that misbehaves on request
# ---------------------------------------------------------------------------


class Link:
    """Carries state packets between two sessions, badly if asked."""

    def __init__(self, clock: FakeClock, drop: float = 0.0,
                 latency: float = 0.0, jitter: float = 0.0,
                 rng: random.Random | None = None) -> None:
        self.clock = clock
        self.drop = drop
        self.latency = latency
        self.jitter = jitter
        self.rng = rng or random.Random(7)
        self._in_flight: list[tuple[float, bytes]] = []
        self.inbox: list = []  # what the receiver's net tick will drain
        self.sent = 0
        self.dropped = 0

    def send(self, payload: bytes) -> None:
        self.sent += 1
        if self.rng.random() < self.drop:
            self.dropped += 1
            return
        delay = max(0.0, self.latency
                    + self.rng.uniform(-self.jitter, self.jitter))
        self._in_flight.append((self.clock.now() + delay, payload))

    def deliver(self) -> None:
        """Move anything whose flight time has elapsed into the inbox.

        Deliberately NOT sorted by arrival: jitter reorders packets, and a
        receiver that only works on ordered input is a receiver that has not
        met a network.
        """
        now = self.clock.now()
        due = [item for item in self._in_flight if item[0] <= now]
        self._in_flight = [item for item in self._in_flight if item[0] > now]
        for arrive_at, payload in due:
            self.inbox.append((payload, arrive_at))

    def drain(self):
        """Stand in for the receive-thread deque the session pops from."""
        class _Popper:
            def __init__(self, items):
                self.items = items

            def popleft(self):
                if not self.items:
                    raise IndexError
                return self.items.pop(0)
        popper = _Popper(list(self.inbox))
        self.inbox.clear()
        return popper


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


class Failure(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


def test_packet_format() -> str:
    """15 bytes, and a round trip that survives quantisation."""
    payload = statepacket.pack_state(
        player_id=1, seq=65534, tick=12345, x=140.0, y=187.4, vy=-951.5,
        flags=statepacket.FLAG_JUMP | statepacket.FLAG_GROUNDED)
    check(len(payload) == 15, f"packet is {len(payload)} bytes, expected 15")

    snap = statepacket.unpack_state(payload)
    check(snap is not None, "packed state did not decode")
    check(snap.player_id == 1 and snap.seq == 65534 and snap.tick == 12345,
          "header round trip lost a field")
    check(abs(snap.y - 187.4) <= 0.05, f"y quantised badly: {snap.y}")
    check(abs(snap.vy - (-951.5)) <= 0.25, f"vy quantised badly: {snap.vy}")
    check(snap.grounded and not snap.alive is False, "flags round trip failed")

    check(statepacket.unpack_state(b"\x01\x02") is None,
          "a short datagram must decode to None, not raise")
    check(statepacket.unpack_state(b"\x99" * 15) is None,
          "a foreign datagram must decode to None")
    return "15 bytes, round trip clean, junk rejected"


def test_seq_wraparound() -> str:
    """Stale, duplicate and wrapped sequence numbers are all handled."""
    check(statepacket.seq_newer(5, 4), "5 should be newer than 4")
    check(not statepacket.seq_newer(4, 5), "4 must not be newer than 5")
    check(not statepacket.seq_newer(5, 5), "a duplicate must not be newer")
    check(statepacket.seq_newer(0, 65535), "0 must be newer than 65535 (wrap)")
    check(statepacket.seq_newer(3, 65530), "3 must be newer than 65530 (wrap)")
    check(not statepacket.seq_newer(65530, 3), "the wrap must not run backwards")

    # And the buffer that uses it.
    buf = PeerBuffer()
    def snap(seq):
        return statepacket.unpack_state(
            statepacket.pack_state(1, seq, seq, 140.0, 0.0, 0.0, 0))
    check(buf.add(snap(10), 1.0), "first packet should be accepted")
    check(not buf.add(snap(9), 1.1), "an older seq must be discarded")
    check(not buf.add(snap(10), 1.2), "a duplicate seq must be discarded")
    check(buf.add(snap(11), 1.3), "a newer seq should be accepted")
    check(len(buf) == 2, f"buffer kept {len(buf)} packets, expected 2")
    return "ordering, duplicates and the 16-bit wrap all correct"


def test_determinism() -> str:
    """Two devices, one seed, identical obstacles -- with different inputs.

    Inputs differ on purpose. If a player's own jumping could perturb the
    spawn stream, the two screens would drift apart the moment somebody played
    differently, which is every run.
    """
    seed = 20260728
    rng_a = random.Random(1)
    rng_b = random.Random(2)

    def run(rng):
        players = [Player(id=0, x=C.PLAYER_START_X),
                   Player(id=1, x=C.PLAYER_START_X + C.PLAYER_SPACING_X)]
        sim = Simulation(seed, players)
        layout = []
        for tick in range(3000):  # 50 seconds
            for player in players:
                player.want_jump = rng.random() < 0.02
                player.want_duck = rng.random() < 0.02
            sim.step(C.TICK_DT)
            # A crash would end one run early and make the comparison
            # meaningless, so keep everyone on their feet: this is a test of
            # the world, not of the physics.
            for player in players:
                player.alive = True
            sim.state.running = True
            for obstacle in sim.state.obstacles:
                layout.append((obstacle.oid, obstacle.kind,
                               round(obstacle.x, 4)))
        return layout

    a, b = run(rng_a), run(rng_b)
    check(len(a) > 100, f"only {len(a)} obstacle samples -- did anything spawn?")
    check(a == b, "the two devices produced different obstacle layouts")
    kinds = len({k for _, k, _ in a})
    return f"{len(a)} obstacle samples identical across {kinds} obstacle types"


# -- the two-session harness -------------------------------------------------


def build_pair(clock: FakeClock, drop=0.0, latency=0.0, jitter=0.0):
    """Two sessions wired to each other through a controllable link."""
    seed = 4242
    reliable: list[tuple[int, dict]] = []

    def roster():
        return [Player(id=0, name="Host", x=C.PLAYER_START_X),
                Player(id=1, name="Joiner",
                       x=C.PLAYER_START_X + C.PLAYER_SPACING_X)]

    link_h2j = Link(clock, drop, latency, jitter, random.Random(11))
    link_j2h = Link(clock, drop, latency, jitter, random.Random(12))

    host = CoopSession(seed, roster(), local_id=0,
                       send_state=link_h2j.send,
                       send_reliable=lambda m: reliable.append((0, m)),
                       is_host=True)
    joiner = CoopSession(seed, roster(), local_id=1,
                         send_state=link_j2h.send,
                         send_reliable=lambda m: reliable.append((1, m)),
                         is_host=False)
    return host, joiner, link_h2j, link_j2h, reliable


def pump_reliable(reliable, host, joiner) -> None:
    """Deliver reliable messages, host-relayed exactly as main.py does."""
    while reliable:
        sender, msg = reliable.pop(0)
        if sender == 0:
            joiner.on_reliable(msg)
        else:
            host.on_reliable(msg)


def test_local_authority() -> str:
    """A jump moves the local dino this frame, with the network switched off.

    The link is set to 100% loss: nothing this device sends arrives anywhere
    and nothing arrives back. If input still works -- and it must -- then there
    is provably no round trip in the input path.
    """
    clock = FakeClock()
    real = install_clock(clock)
    try:
        host, joiner, h2j, j2h, reliable = build_pair(clock, drop=1.0)
        # Clear the start countdown.
        for _ in range(40):
            clock.advance(FRAME_DT)
            host.advance(FRAME_DT)
            joiner.advance(FRAME_DT)

        dino = joiner.local_player()
        check(dino.grounded, "the joiner's dino should start on the ground")

        joiner.set_local_input(jump=True, duck=False)
        clock.advance(FRAME_DT)
        joiner.advance(FRAME_DT)

        check(not dino.grounded,
              "the joiner's dino did not leave the ground on the input frame")
        check(dino.vy > 0.0, f"expected upward velocity, got vy={dino.vy}")
        check(j2h.dropped == j2h.sent,
              "the test link was supposed to drop everything")
        rise = dino.y
        check(rise > 0.0, "the dino gained no height on the first frame")
        return (f"airborne on the input frame (vy={dino.vy:.0f}, "
                f"y={rise:.1f}px) with 100% packet loss")
    finally:
        timing.now = real


def _keep_alive(session) -> None:
    """Undo any crash, so a long run stays a long run.

    Without this the dinos hit a cactus after a few hundred frames, the run
    ends, and the world tick stops advancing while packets keep flowing --
    which is a perfectly correct thing for the game to do and completely
    useless to measure. The first version of this harness spent 60% of its
    frames sampling a dead run and reported the resulting garbage as a
    100 px teleport.
    """
    state = session.state
    state.running = True
    state.cause_player_id = None
    state.cause_obstacle = None
    for player in state.players:
        player.alive = True
    state.obstacles.clear()


def _run_pair(clock, frames: int, drop=0.0, latency=0.0, jitter=0.0,
              jump_at=frozenset()):
    """Run two sessions side by side and record what each one saw."""
    host, joiner, h2j, j2h, reliable = build_pair(clock, drop, latency, jitter)
    net_accum = 0.0
    net_dt = 1.0 / 20.0
    partner_track = []   # joiner's view of the host dino, per frame
    local_track = []     # joiner's own dino, per frame

    for frame in range(frames):
        clock.advance(FRAME_DT)
        # Host jumps on a schedule so there is real vertical motion to track.
        host.set_local_input(jump=frame in jump_at, duck=False)
        joiner.set_local_input(jump=False, duck=False)

        h2j.deliver()
        j2h.deliver()

        net_accum += FRAME_DT
        while net_accum >= net_dt:
            net_accum -= net_dt
            host.net_tick(j2h.drain())
            joiner.net_tick(h2j.drain())

        host.advance(FRAME_DT)
        joiner.advance(FRAME_DT)
        pump_reliable(reliable, host, joiner)
        _keep_alive(host)
        _keep_alive(joiner)

        remote = next(p for p in joiner.state.players if p.id == 0)
        partner_track.append(remote.y)
        local_track.append(joiner.local_player().y)

    return host, joiner, partner_track, local_track, h2j


def test_partner_smoothness() -> str:
    """Under loss and jitter the partner still moves like a dino.

    The measure is the largest single-frame move. A dino at the fastest point
    of a jump covers ~16 px in a frame, so anything past a generous multiple of
    that is a teleport -- which is the failure this whole buffer exists to
    prevent.
    """
    jumps = frozenset(range(30, 600, 45))

    clock = FakeClock()
    real = install_clock(clock)
    try:
        _, _, clean_partner, clean_local, _ = _run_pair(
            clock, 600, jump_at=jumps)
        clean_step = max(abs(b - a) for a, b in
                         zip(clean_partner, clean_partner[1:]))

        clock2 = FakeClock()
        timing.now = clock2.now
        _, _, lossy_partner, lossy_local, link = _run_pair(
            clock2, 600, drop=0.20, latency=0.05, jitter=0.03, jump_at=jumps)
        lossy_step = max(abs(b - a) for a, b in
                         zip(lossy_partner, lossy_partner[1:]))
    finally:
        timing.now = real

    moved = max(clean_partner)
    check(moved > 50.0,
          f"the partner barely moved ({moved:.1f}px) -- the test proves nothing")
    # A dino at the fastest point of a jump covers ~16 px in a frame. Twice
    # that is the ceiling for "still reads as motion"; the pre-smoothing code
    # hit 97 px here, which reads as a teleport because it is one.
    check(lossy_step < 32.0,
          f"partner teleported {lossy_step:.1f}px in one frame under loss")
    check(lossy_local == clean_local,
          "the local dino changed when the NETWORK got worse -- it must not "
          "depend on the network at all")

    loss = 100.0 * link.dropped / max(1, link.sent)
    return (f"{loss:.0f}% loss + 30ms jitter: partner max step "
            f"{lossy_step:.1f}px (clean {clean_step:.1f}px), "
            f"local dino bit-identical")


def test_severe_loss_degrades() -> str:
    """At 50% loss it should get worse gracefully, not fall over.

    Half the stream gone is well past anything a working Wi-Fi network does;
    the bar here is only that the partner stays on a plausible path and the
    local dino stays exactly as responsive as it was on a perfect link.
    """
    jumps = frozenset(range(30, 600, 45))
    clock = FakeClock()
    real = install_clock(clock)
    try:
        _, _, _, clean_local, _ = _run_pair(clock, 600, jump_at=jumps)
        clock2 = FakeClock()
        timing.now = clock2.now
        _, joiner, partner, local, link = _run_pair(
            clock2, 600, drop=0.50, latency=0.08, jitter=0.04, jump_at=jumps)
    finally:
        timing.now = real

    step = max(abs(b - a) for a, b in zip(partner, partner[1:]))
    check(step < 55.0, f"partner jumped {step:.1f}px at 50% loss")
    check(local == clean_local,
          "the local dino was affected by the partner's connection")
    check(min(partner) >= 0.0, "the partner was rendered below the ground")
    loss = 100.0 * link.dropped / max(1, link.sent)
    return (f"{loss:.0f}% loss + 40ms jitter: partner max step {step:.1f}px, "
            f"never below ground, local dino untouched")


def test_extrapolation_then_hold() -> str:
    """A dead stream extrapolates briefly, then holds instead of running away."""
    clock = FakeClock()
    real = install_clock(clock)
    try:
        buf = PeerBuffer()
        # A dino at the top of a jump: moving up, about to come down.
        for i in range(3):
            payload = statepacket.pack_state(
                0, i, i, 140.0, 100.0 + i * 5.0, 300.0,
                statepacket.FLAG_JUMP)
            buf.add(statepacket.unpack_state(payload),
                    clock.now() + i * 0.05)
        clock.advance(0.15)

        base = buf.sample(clock.now())
        check(base is not None, "a filled buffer sampled to None")

        # Now the stream dies.
        clock.advance(statepacket.EXTRAP_MAX * 0.5)
        during = buf.sample(clock.now())
        check(buf.quality == "extrapolating",
              f"expected extrapolation, got {buf.quality!r}")

        clock.advance(statepacket.EXTRAP_MAX * 4.0)
        after = buf.sample(clock.now())
        check(buf.quality == "stale",
              f"expected a hold after EXTRAP_MAX, got {buf.quality!r}")

        clock.advance(10.0)
        much_later = buf.sample(clock.now())
        check(abs(much_later.y - after.y) < 0.001,
              "the held position kept drifting -- it must freeze, not creep")
        check(much_later.y >= 0.0, "extrapolation pushed the dino underground")
        return (f"extrapolated {during.y - base.y:+.1f}px, then held at "
                f"{after.y:.1f}px for 10s without drifting")
    finally:
        timing.now = real


def test_death_is_announced() -> str:
    """A local death is detected locally and ends the run on both devices."""
    clock = FakeClock()
    real = install_clock(clock)
    try:
        host, joiner, h2j, j2h, reliable = build_pair(clock)
        for _ in range(40):  # clear the countdown
            clock.advance(FRAME_DT)
            host.advance(FRAME_DT)
            joiner.advance(FRAME_DT)

        # Kill the joiner's dino the way the world would: put a cactus on it.
        dino = joiner.local_player()
        from game.entities import Obstacle
        joiner.state.obstacles.append(
            Obstacle(x=dino.x, y=0.0, kind="CACTUS_SMALL", oid=999))

        clock.advance(FRAME_DT)
        joiner.advance(FRAME_DT)
        check(not joiner.running, "the joiner's own collision did not kill it")

        pump_reliable(reliable, host, joiner)
        clock.advance(FRAME_DT)
        host.advance(FRAME_DT)
        check(not host.running,
              "the host kept running after being told its partner died")
        check(host.state.cause_player_id == 1,
              f"host blamed player {host.state.cause_player_id}, expected 1")
        return "local detection -> reliable announce -> both runs ended"
    finally:
        timing.now = real


def test_powerups_still_apply() -> str:
    """A pickup reaches the team -- solo, and over the network.

    step() reports a touched power-up instead of applying it, so that a
    networked pair can start the effect on a tick they agree on. That change
    silently breaks single player if the solo loop is not updated to apply what
    it is handed, and a solo run with no power-ups is a bug nobody would
    attribute to netcode.
    """
    from game.entities import PowerUp

    # -- solo: claim and apply are the same instant -------------------------
    players = [Player(id=0, x=C.PLAYER_START_X)]
    sim = Simulation(1234, players)
    sim.state.powerups.append(
        PowerUp(x=players[0].x, y=0.0, kind=C.POWERUP_SHIELD, pid=7))
    claimed = sim.step(C.TICK_DT)
    check(len(claimed) == 1, f"solo touch reported {len(claimed)} claims")
    check(not sim.state.shield, "step() must not apply the effect itself")
    for powerup in claimed:
        sim.apply_powerup(powerup.kind, powerup.pid)
    check(sim.state.shield, "the solo loop did not apply the shield")
    check(not sim.state.powerups, "the collected power-up stayed on the field")

    # -- networked: claim -> host grants a tick -> both apply there ---------
    clock = FakeClock()
    real = install_clock(clock)
    try:
        host, joiner, h2j, j2h, reliable = build_pair(clock)
        for _ in range(40):
            clock.advance(FRAME_DT)
            host.advance(FRAME_DT)
            joiner.advance(FRAME_DT)

        dino = joiner.local_player()
        for session in (host, joiner):
            session.state.powerups.append(
                PowerUp(x=dino.x, y=dino.y, kind=C.POWERUP_SLOWMO, pid=42))

        clock.advance(FRAME_DT)
        joiner.advance(FRAME_DT)
        check(C.POWERUP_SLOWMO not in joiner.state.effects,
              "the joiner applied a power-up before the host had ruled on it")
        pump_reliable(reliable, host, joiner)

        applied_at = {}
        for _ in range(C.WORLD_EVENT_LEAD + 10):
            clock.advance(FRAME_DT)
            host.advance(FRAME_DT)
            joiner.advance(FRAME_DT)
            pump_reliable(reliable, host, joiner)
            for name, session in (("host", host), ("joiner", joiner)):
                if name not in applied_at and \
                        C.POWERUP_SLOWMO in session.state.effects:
                    applied_at[name] = session.state.tick

        check(len(applied_at) == 2,
              f"only {sorted(applied_at)} applied the power-up")
        check(applied_at["host"] == applied_at["joiner"],
              f"applied on different ticks: {applied_at} -- the two worlds "
              "will now scroll differently")
        return (f"solo applies immediately; networked pair both applied on "
                f"tick {applied_at['host']}")
    finally:
        timing.now = real


def test_peer_loss_slacks_the_rope() -> str:
    """A partner who goes silent stops being able to drag the team."""
    from game import physics
    a = Player(id=0, x=0.0, y=0.0, vy=0.0)
    b = Player(id=1, x=110.0, y=C.MAX_STRETCH * 1.5, vy=0.0)

    physics.apply_rope_forces([a, b], C.TICK_DT)
    check(a.vy > 0.0, "a stretched rope should pull the low dino up")

    a.vy = b.vy = 0.0
    b.connected = False
    physics.apply_rope_forces([a, b], C.TICK_DT)
    check(a.vy == 0.0,
          f"a disconnected partner still pulled (vy={a.vy}) -- the rope must "
          "go slack rather than anchor the team to a frozen dino")
    check(physics.rope_tension([a, b], 0) == 0.0,
          "the tension meter disagrees with the physics")
    return "rope pulls when connected, goes slack when the peer is gone"


class Skipped(Exception):
    """Not a failure -- the environment could not run this one."""


def test_real_sockets() -> str:
    """The actual transport, over real loopback sockets. No Kivy.

    Everything above runs the sessions in one process with the network faked,
    which is the only way to test timing but proves nothing about the sockets.
    This is the other half: a real GameHost, a real GameClient, real TCP
    framing, real UDP registration, real datagrams both ways.

    Binds the game's fixed ports, so it is skipped rather than failed if
    something (an instance of the game, most likely) already has them.
    """
    import socket
    import time

    from net.client import GameClient
    from net.host import GameHost

    host = GameHost(name="TestHost")
    try:
        host.start()
    except OSError as exc:
        raise Skipped(f"port {C.PORT_GAME} busy ({exc})") from exc
    if host._udp is None:
        host.stop()
        raise Skipped(f"UDP port {C.PORT_GAME_UDP} busy")

    client = GameClient(name="TestJoiner", skin=1)
    try:
        try:
            client.connect("127.0.0.1", C.PORT_GAME)
        except OSError as exc:
            raise Skipped(f"could not connect to loopback ({exc})") from exc

        # The host answers a JOIN with the roster on the socket thread, so the
        # id arrives without anything having to pump a queue.
        deadline = time.monotonic() + 3.0
        while client.player_id is None and time.monotonic() < deadline:
            time.sleep(0.01)
        check(client.player_id == 1,
              f"joiner got id {client.player_id}, expected 1")

        # Joiner -> host. send_state also carries the UDP registration until
        # the first packet comes back, which is what teaches the host our
        # ephemeral port.
        payload = statepacket.pack_state(1, 1, 60, 250.0, 42.5, -120.0,
                                         statepacket.FLAG_GROUNDED)
        deadline = time.monotonic() + 3.0
        while not host.state_inbox and time.monotonic() < deadline:
            client.send_state(payload)
            time.sleep(0.02)
        check(bool(host.state_inbox), "no state packet reached the host")

        got, _at = host.state_inbox[-1]
        snap = statepacket.unpack_state(got)
        check(snap is not None and snap.player_id == 1,
              "the host received something that was not our state packet")
        check(abs(snap.y - 42.5) < 0.05,
              f"y arrived as {snap.y}, sent 42.5")

        # Host -> joiner, to the endpoint it just learned.
        back = statepacket.pack_state(0, 1, 61, 140.0, 99.0, 250.0, 0)
        deadline = time.monotonic() + 3.0
        while not client.state_inbox and time.monotonic() < deadline:
            host.send_state(back)
            time.sleep(0.02)
        check(bool(client.state_inbox), "no state packet reached the joiner")
        snap = statepacket.unpack_state(client.state_inbox[-1][0])
        check(snap is not None and snap.player_id == 0 and abs(snap.y - 99.0) < 0.05,
              "the joiner received the wrong packet")

        # And the reliable channel, in the direction that carries a death.
        client.send({"type": "death", "id": 1, "tick": 61, "obstacle": "CACTUS_SMALL"})
        deadline = time.monotonic() + 3.0
        seen = None
        while seen is None and time.monotonic() < deadline:
            try:
                _cid, msg = host.inbox.get(timeout=0.05)
            except Exception:  # noqa: BLE001 -- queue.Empty, by any name
                continue
            if msg.get("type") == "death":
                seen = msg
        check(seen is not None, "the reliable death never reached the host")
        check(seen["id"] == 1, "the reliable message arrived corrupted")

        return ("real sockets: id assigned, 15-byte state both ways, "
                "reliable death delivered")
    finally:
        client.disconnect()
        host.stop()
        # Give the OS a moment to release the listener before anything else
        # tries to bind it.
        time.sleep(0.1)


CHECKS = [
    ("packet format", test_packet_format),
    ("sequence numbers", test_seq_wraparound),
    ("world determinism", test_determinism),
    ("local authority", test_local_authority),
    ("partner smoothness", test_partner_smoothness),
    ("severe loss", test_severe_loss_degrades),
    ("extrapolate then hold", test_extrapolation_then_hold),
    ("death announcement", test_death_is_announced),
    ("power-ups", test_powerups_still_apply),
    ("peer loss / rope", test_peer_loss_slacks_the_rope),
    ("real sockets", test_real_sockets),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    failures = 0
    skipped = 0
    width = max(len(name) for name, _ in CHECKS)
    for name, fn in CHECKS:
        try:
            detail = fn()
        except Skipped as exc:
            skipped += 1
            print(f"skip  {name:<{width}}  {exc}")
        except Failure as exc:
            failures += 1
            print(f"FAIL  {name:<{width}}  {exc}")
        except Exception as exc:  # noqa: BLE001 -- a broken test is a failure
            failures += 1
            print(f"ERROR {name:<{width}}  {type(exc).__name__}: {exc}")
            if args.verbose:
                import traceback
                traceback.print_exc()
        else:
            print(f"ok    {name:<{width}}  {detail}")

    print()
    passed = len(CHECKS) - failures - skipped
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"{passed}/{len(CHECKS) - skipped} checks passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
