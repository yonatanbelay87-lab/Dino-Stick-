# Netcode

How two phones play one game of Dino Stick, and why it is built this way.

## The model, in one paragraph

**Every device is authoritative over its own dino and nothing else.** Your
input moves your dino on the frame you pressed it — no packet is sent first and
no reply is waited for. Your partner is a stream of snapshots, rendered 100 ms
in the past and interpolated so it moves smoothly. The obstacles are not sent at
all: both devices generate them from a shared integer seed, so they are the same
obstacles by construction rather than by agreement.

There is **no reconciliation and no lag compensation**, because there is nothing
to reconcile against. Those are answers to "the server disagrees with you", and
here there is no server.

This works because the game is co-operative. Nobody gains by lying about where
their own dino is; the only thing you could cheat yourself into is dying. A
competitive game would need a host that re-simulates and overrules, and would
pay input latency for the privilege.

## What that replaced

The previous design was host-authoritative: the host ran the only simulation and
broadcast the whole world — obstacles, power-ups, score, every player — 60 times
a second. It had client-side prediction and interpolation bolted on, and it was
genuinely good, but the joiner's dino was still not theirs. Their collisions
were ruled on by the host, so a hit registered a round trip late and prediction
then had to snap the dino back. That snap is the "rubber-band" this replaced.

`game/prediction.py` (the predictor) and the snapshot/interpolate helpers in
`game/entities.py` were deleted with it. Prediction and reconciliation exist to
paper over an authority that lives somewhere else; move the authority to the
device holding the controls and there is nothing left for them to do.

## The pieces

| File | What it owns |
|---|---|
| `net/statepacket.py` | The 15-byte state packet, and every tunable for the unreliable path |
| `net/peers.py` | Snapshot buffering, interpolation, extrapolation, the loss simulator |
| `net/protocol.py` | The reliable channel: message types and TCP framing |
| `net/host.py` | Listener, relay, seed. **Not** a simulation any more |
| `net/client.py` | The joiner's two sockets. Symmetrical with the host |
| `game/coop.py` | `CoopSession` — one per device, host and joiner alike |
| `game/simulation.py` | The world. Local dinos are simulated, remote ones are puppets |
| `../tools/net_selftest.py` | All of the above, checked headlessly |

## The two transports

The split is by how much a message matters, not by convenience.

**UDP, 20 Hz, fire and forget** — the state stream. One packet per device per
tick, 15 bytes, no acknowledgement and no retransmission. A lost one is
superseded 50 ms later by definition, so waiting for it would cost more than
losing it.

```
!BBHIhhhB   15 bytes
  B  msg_type    always 1 (STATE)
  B  player_id   0 = host, 1..n = joiners
  H  seq         per-sender, wraps at 65535
  I  tick        the sender's world tick
  h  x           position * 10   (0.1 px)
  h  y           position * 10
  h  vy          velocity * 2    (0.5 px/s)
  B  flags       jump | duck | dead | grounded | ducking
```

**TCP, rare, JSON** — things that must arrive exactly once and in order:
`JOIN` (the HELLO), `SEED`, `START`, `DEATH`, `REVIVE`, `POWERUP`,
`POWERUP_CLAIM`, `SHIELD`, `SCORE_SYNC`, `GAME_OVER`, `REMATCH`. All
low-frequency, so a compact encoding would buy nothing and cost readability in
a packet capture at 2am.

Put position on the reliable channel and one dropped packet head-of-line-blocks
every position behind it. Put a death on the unreliable one and someone
occasionally dies with no sound and no particles. The split is the design.

## Two loops, deliberately different rates

Render runs at the display rate (`Clock.schedule_interval(self._frame, 0)`).
The network tick runs at `NET_TICK_HZ` = 20
(`Clock.schedule_interval(self._net_tick, 1/20)`) and does two things: send this
device's dino, drain everything the receive thread has queued.

**Interpolation happens every render frame, not every net tick.** That is what
turns 20 arrivals a second into 60 smooth frames.

Sending on the render loop would tie packet rate to framerate — a phone dropping
to 40 fps would quietly start sending 40 snapshots a second — and draining on it
turns a burst of queued packets into a dropped frame, exactly when the network
is already struggling.

## Threading

One daemon thread per socket. It parses nothing and decides nothing: it appends
`(payload, arrival_time)` to a `deque` and goes back to waiting. The Kivy thread
pops from that deque at 20 Hz.

Sockets are non-blocking (`setblocking(False)`) and parked on a `selectors`
selector with a 0.5 s timeout, which gives both properties at once — nothing can
block, and nothing spins a CPU core while idle. **No blocking socket call ever
happens on the Kivy thread.** The one blocking call in the codebase is
`GameClient.connect`, which runs on a menu button press.

No locks: `deque.append` and `deque.popleft` are atomic under the GIL, and the
two sides never touch the same end.

## The deterministic world

`World` (`game/world.py`) draws every spawn decision from one seeded
`random.Random` in a fixed order. Given the same seed and the same tick count,
two devices produce byte-identical obstacles. The host picks the seed with
`random.randrange(2**31)` and sends it once.

Two things protect that, because both would otherwise break it silently:

**Synchronised start.** `START` carries a countdown (`START_COUNTDOWN`, 0.4 s)
rather than meaning "now". "Now" arrives half a round trip late, and in a world
where the tick number *is* the obstacle layout, starting late means being
permanently offset. The joiner subtracts its measured one-way latency from the
countdown, so both reach tick 0 together rather than receiving the same message
together.

**Scheduled world events.** A power-up that slows the world or bends gravity
changes where the next obstacle spawns, so both devices must apply it on the
*same tick*. Applying it "when I noticed" cannot do that. So a device that
touches one **claims** it; the host **grants** it with an `apply_tick` set
`WORLD_EVENT_LEAD` (12 ticks ≈ 200 ms) ahead; everyone applies it there. A
device that still receives it late applies it and subtracts the scroll that
should not have happened.

Clocks drift regardless — two devices free-running 60 Hz accumulators diverge by
a tick every few seconds — so the joiner eases its world clock toward the host's
at one tick per frame (`WORLD_SYNC_DEADBAND`, `WORLD_SYNC_SNAP`). `SCORE_SYNC`
once a second is the belt-and-braces check on top.

## The rope: the one honest compromise

`physics.apply_rope_forces` couples your vertical velocity to your partner's
height. Under peer authority the only partner height available is the one being
*rendered* — 100 ms in the past. So a tug is felt slightly after it was given.

The alternative would be predicting the partner's motion to "now", which means
inventing their jumps: confident, wrong motion that then has to be yanked back.
Worse, the rope would then pull you toward a position that is not where the rope
is drawn.

So the rope reads the partner as rendered. **What you see pulling you is what
pulls you.** On a LAN this is imperceptible; on a bad connection the tug lags,
which is the honest failure and the one that still looks like a rope.

If a peer's stream goes quiet for `PEER_ROPE_TIMEOUT` (1 s) the rope goes slack
for them and their dino is drawn faded. A frozen partner that still pulls is a
frozen partner that drags the team into the next cactus.

## Tuning `INTERP_DELAY` for a worse connection

`INTERP_DELAY` (`net/statepacket.py`, default **0.10 s**) is how far in the past
partners are drawn. It is the single most important knob here.

**The rule: it must comfortably exceed the largest gap you expect between two
arriving packets.** Below that there is routinely no newer snapshot to
interpolate *toward*, so the partner freezes and then catches up. Above it,
partners are further behind where they really are for no benefit.

The floor is one send interval — 50 ms at `NET_TICK_HZ` = 20. The default of two
intervals rides out a single dropped packet with a bracketing pair still in
hand.

| Connection | Suggested | Why |
|---|---|---|
| Wired / same-room Wi-Fi | 0.08 | Loss near zero; buy back 20 ms |
| Default LAN | **0.10** | One dropped packet still brackets |
| Busy Wi-Fi, some loss | 0.15 | Rides out two consecutive drops |
| Hotspot / congested | 0.20–0.25 | Three drops; partners visibly behind |

It was 66 ms under the old design and the number went **up** when the send rate
went **down** (60 Hz → 20 Hz). Delay is measured in packet intervals, not
milliseconds — a faster stream needs a shorter delay for the same smoothness.

Related knobs:

- **`EXTRAP_MAX`** (0.25 s) — how long to keep dead-reckoning a silent partner
  before holding still. Raise it and a bad connection guesses further and is
  more visibly wrong when corrected; lower it and partners freeze sooner but
  more honestly. Extrapolation integrates gravity, not just velocity: a dino's
  vy is never constant for more than a tick, and extending it linearly launches
  the partner off the top of the screen at the apex of a jump.
- **`PEER_BUFFER`** (12) — snapshots retained per peer, 600 ms of history. Must
  comfortably exceed `INTERP_DELAY`. Raise it with the delay.
- **`NET_TICK_HZ`** (20) — raising it is usually a better spend than raising
  `INTERP_DELAY`, because a faster stream shrinks the gaps the delay has to
  cover. Costs bandwidth: 15 bytes × 20/s × peers ≈ 300 B/s each way.
- **`POS_SCALE` / `VEL_SCALE`** (10 / 2) — fixed-point scales. Both pack into
  int16, giving ±3276 px and ±16383 px/s against a jump peak of ~188 px and a
  launch velocity of 950 px/s.

## Two findings worth knowing before you change `net/peers.py`

Both were measured, both were counter-intuitive, and both are load-bearing.

**1. Interpolate against the sender's tick, not against arrival time.**
Tagging snapshots with arrival time and interpolating between those is the
obvious first cut — it needs no clock sync at all, which is exactly what you
want between two phones. It also converts network jitter directly into playback
speed: two snapshots 100 ms of *motion* apart can arrive 40 ms apart, and
replaying that gap at arrival speed runs the partner at 2.5×. Measured, a
partner descending at 13 px/frame was rendered at 3.9 px/frame for six frames
and then lurched 38 px.

The fix needs no clock sync either. Every packet already carries the sender's
world tick, so the time *between* two snapshots is exactly `(tick_b - tick_a) *
TICK_DT`. Arrival time is then used for one thing: a single smoothed offset
placing that sequence on our clock. Jitter lands in the offset, where the delay
absorbs it, instead of in the playback rate, where you can see it.

**2. Do not drop reordered packets.** "Discard anything whose seq is not newer
than the newest seen" is right for a latest-wins renderer and wrong for an
interpolating buffer. At 50 ms spacing with ±30 ms of jitter, reordering is
constant, and discarding those packets on top of the ones the link actually lost
produced 200 ms holes. One of them swallowed a partner's entire takeoff: the
dino sat on the ground for nine frames and then appeared 97 px in the air.

What that rule really protects against is rendering *backwards*, and a
tick-based timeline gets that for free — a late packet knows exactly where it
belongs. So duplicates are dropped, packets the render target has already passed
are dropped, and everything else is inserted at its own place whatever order it
arrived in. The offset is still only ever learned from packets that *are* the
newest, because a late packet's arrival time is late by definition.

A long enough gap must still be caught up on eventually. When the correction is
larger than plausible motion it is absorbed into an offset that decays over
`_SMOOTH_TIME` (0.12 s), so the partner is rendered along the true path from a
slightly wrong place rather than teleported onto it. In the steady state the
offset is zero, so this adds no lag to a healthy stream.

## Testing

```
python tools/net_selftest.py       # from the repo root
```

Two halves, eleven checks, no Kivy and no window.

Most of it runs two complete `CoopSession`s against each other in one process
over an in-memory link that drops, delays and jitters on demand — the only way
to make timing failures reproducible. It checks packet size and round trip,
sequence wraparound and duplicate rejection, world determinism across 50 s with
*different* inputs on each device, local input working with the network 100%
dropped, partner smoothness under 20% and 50% loss, extrapolate-then-hold, death
announcement, power-ups applying on the same tick on both devices, and the rope
slackening when a peer goes quiet.

The last check uses **real loopback sockets**: a real `GameHost`, a real
`GameClient`, real TCP framing, real UDP registration and real datagrams both
ways. It is skipped rather than failed if the game's ports are already taken,
which usually means an instance of the game is running.

A run against a fixed clock, so a "250 ms stall" costs no wall time:

```
ok    world determinism      4510 obstacle samples identical across 3 obstacle types
ok    local authority        airborne on the input frame (vy=950, y=15.8px) with 100% packet loss
ok    partner smoothness     18% loss + 30ms jitter: partner max step 23.9px (clean 13.5px), local dino bit-identical
ok    severe loss            50% loss + 40ms jitter: partner max step 22.8px, never below ground, local dino untouched
ok    power-ups              solo applies immediately; networked pair both applied on tick 52
ok    real sockets           real sockets: id assigned, 15-byte state both ways, reliable death delivered
```

"Local dino bit-identical" is the acceptance criterion stated as an assertion:
the joiner's own dino produces the same sequence of positions whether the link
is perfect or dropping half its packets, because it never consults the link.

To break a *real* game on purpose, set the knobs in `game/constants.py`:

```python
NET_SIM_DROP    = 0.15   # fraction of outbound state packets thrown away
NET_SIM_LATENCY = 0.08   # s of added one-way delay
NET_SIM_JITTER  = 0.04   # s of +- randomness on that delay
```

These wrap the **outbound** stream (`net/peers.py`, `LossyLink`), which is the
honest place for them: an inbound-only drop cannot reproduce jitter or
reordering, and reordering is what exercises the buffer. Your own dino should
be completely unaffected — it never touches the network.

Two processes on one machine work: the joiner's UDP port is ephemeral, so run
one instance, host, then run a second and join `127.0.0.1`.

## Android

`INTERNET` is in `android.permissions` (`buildozer.spec`), along with
`ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE` and `CHANGE_WIFI_MULTICAST_STATE` —
the last is required to hold the `MulticastLock` without which Android silently
drops the UDP broadcasts that LAN discovery depends on (`net/discovery.py`).
Raw TCP/UDP on the LAN is not affected by the cleartext-HTTP block, so no
network security config is needed.

## What is deliberately not here

- **Reconciliation / rollback.** No authority to reconcile against.
- **Server-side lag compensation.** No server.
- **Clock synchronisation.** Never needed: the only wall clock ever read is our
  own, and the peer's tick is a counter, not a time.
- **Obstacles on the wire.** They come from the seed. If you find yourself
  wanting to send one, the determinism has broken somewhere and sending it will
  hide the cause rather than fix it.
- **A UI for `REVIVE`.** The message, the handler and the state change all exist
  and work; nothing triggers them, because a crash currently ends the run for
  everyone. Adding a revive mechanic is a UI change, not a netcode change.
