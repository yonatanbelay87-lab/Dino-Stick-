# Dino Stick

A local-multiplayer co-op endless runner. 2–4 dinos, all tied together by an
elastic rope. The rope exerts real spring forces, so jumping out of sync drags
your teammates. If any one player crashes, **everyone** loses.

LAN only — UDP broadcast for discovery, TCP for control, UDP for gameplay. No
servers, no accounts.

## Status

- [x] **Phase 0** — scaffold: app launches, screens navigate
- [x] **Phase 1** — single-player core runner
      (fixed 60 Hz sim, jump/duck, seeded cacti, difficulty ramp, game over)
- [x] **Phase 2** — elastic rope, two local dinos, shared fate
- [x] **Phase 3** — host/client networking over LAN, lobby, discovery
- [x] **Phase 4** — obstacles, power-ups, skins, HUD, juice
- [~] **Phase 5** — Android packaging: code complete and tested on desktop
      (touch zones, multicast lock, `buildozer.spec`). **The APK itself has not
      been built and no device test has been run** — see below.

## Run on desktop

**Windows, easiest:** double-click `run.bat` in the repo root. It creates the
virtual environment on first run, then starts the game.

Or run it directly — no need to activate the venv:

```powershell
.\.venv\Scripts\python.exe dinostick\main.py
```

### First-time setup by hand

Requires Python 3.12 — Kivy 2.3.1 has no wheels for 3.14, which is why the
launcher pins `py -3.12`.

```bash
py -3.12 -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on Linux/macOS
pip install "kivy[base]==2.3.1"

python dinostick/main.py
```

### Controls

| | Jump | Duck |
|---|---|---|
| Player 1 | `Space` / `W` | `S` |
| Player 2 | `Up` | `Down` |

Arrow keys are reserved for player 2 from the start, so same-screen co-op in
Phase 2 needs no re-binding. Ducking mid-air is a fast-fall — the only way to
cut a jump short once committed.

### Multiplayer testing on one machine

Open two terminals and run `python main.py` in each.

1. First window: **Host Game** → the lobby shows the address to join at.
2. Second window: **Join Game** → pick the discovered game, or type
   `127.0.0.1` in the manual IP box.
3. Client presses **Ready**; the host's **Start Game** button lights up.

Across two devices, use the host's real LAN address (shown in its lobby)
instead of `127.0.0.1`. Both must be on the same Wi-Fi or hotspot.

**Play** on the menu skips networking entirely: two roped dinos on one keyboard
on desktop — the quickest way to feel the rope — or a solo run on a phone,
where touch only ever drives one dino.

## The rope

A damped vertical spring between each neighbouring pair, applied *before* the
ground clamp so a partner's weight can drag on a grounded dino.

**The rope tethers players; it does not drive them.** Everyone plays their own
run, reacting to what is in front of their own dino, and feels the others as
weight. Failure is the shared part: one crash ends the run for everyone
(`Simulation._check_collisions`), so the tension is social rather than
mechanical.

### The retune, and why it was needed

The original tuning (`K_ROPE=16`, `MAX_STRETCH=40`) contradicted the game's own
geometry. Players stand `PLAYER_SPACING_X` apart, so the same cactus reaches
them at different moments and they are **forced** out of sync:

| speed | forced offset between jumps |
|---|---|
| `BASE_SPEED` (start) | **262 ms** |
| 700 px/s (mid-run) | 157 ms |
| `MAX_SPEED` | 100 ms |

…while the rope's cliff sat at ~150 ms. Correct play was punished from the
first obstacle. Two bots each playing their own game averaged **18 m** against
**371 m** solo — the rope was eating 95% of the run.

Retuned against that geometry (`K_ROPE=6`, `ROPE_DAMP=0.2`,
`MAX_STRETCH=170`). Apexes reached when the second dino jumps late, against a
196px free jump and an 82px large cactus:

| desync | before | after | |
|---|---|---|---|
| 0 ms | 196 | 196 | in sync, rope never engages |
| 157 ms | **77 ✗** | 164 | the forced offset at speed |
| 262 ms | **77 ✗** | 156 | the forced offset at the start |
| 500 ms | 83 | 156 | properly out of sync, still survivable |

(✗ = clips a large cactus, i.e. an unavoidable death.)

Two independent bots now average **365 m — 98% of solo**. The rope costs 2% of
the run instead of 95%.

### It is still a rope

- **It holds you back.** Jumping while your partner is grounded costs 40px of
  height — a fifth of your jump — clearly felt, never fatal.
- **It slingshots.** The late jumper is pulled *up* by the partner's weight,
  reaching 202–211px against a 196px free jump. Being second has an upside.
- **It still runs out.** Pairs reach 194px apart against the 170px
  `MAX_STRETCH`, so the stiffening branch is live at the top of the range.
- **Shared fate is unchanged.** Any dino touching an obstacle ends the run for
  everyone; a shield still absorbs one hit for the whole team.

`MAX_STRETCH` is a UI number as much as a physics one — `rope_tension()`
divides by it to drive the HUD meter and the screen shake. Setting it to the
120 the physics alone would have liked pins the meter red 13% of the time and
shakes the screen 67 times a minute. 170 keeps the meter meaningful (pinned
1.1%) and the shake rare enough to mean something (16/min). Above ~196 the
stiffening branch becomes unreachable and dies.

## Obstacles and power-ups

Six obstacle types, faded in as the run gets faster: small/large cactus,
cactus cluster, boulder, and birds at two heights. The bird heights are picked
against the measured jump apexes, so "duck" genuinely means duck:

| | occupies | standing (0–60) | ducking (0–32) | jump over it? |
|---|---|---|---|---|
| `BIRD_LOW` | 26–60 | hit | hit | **must jump** (clears at 60) |
| `BIRD_HIGH` | 46–86 | hit | safe | solo jump (83) fails, synced (196) clears |

Ducking needs both feet on the ground — so a bird arriving while the rope has
you airborne is lethal. That is the point.

Power-ups are team-wide and float at 70–160px, above a solo jump, so
collecting the good ones is itself a reason to coordinate. Shield absorbs one
fatal hit for everyone; Slow-mo, Feather (low gravity + slack rope) and Sync
(rope fully slack) are timed; Star is a score bonus.

### The fairness invariant

The spawner guarantees it never produces a gap the team cannot clear. Gaps are
sized in **pixels** as a multiple of one jump arc, plus the team's own width,
plus the obstacle's width — then converted to seconds at the current scroll
speed. This is verified directly, not by proxy: 0 unclearable gaps across
1–4 players × 25 seeds × ~11,500 obstacle pairs.

Sizing gaps in seconds instead is a trap worth knowing about. While Slow-mo is
active, a perfectly correct *time* gap lays obstacles closer together *in
space*; once the effect expires they arrive at full speed packed tighter than
one jump arc, killing you a couple of seconds later with nothing on screen to
explain why.

## Architecture

- **Host-authoritative.** The host simulates everything — all players' physics,
  the rope, spawning, collisions, score. Clients send only `jump`/`duck` and
  render broadcast snapshots. The rope couples players, so the sim has to be a
  single source of truth or it desyncs.
- **Fixed 60 Hz timestep** via an accumulator, decoupled from render framerate.
- **Seeded world** — one RNG drives every spawn; the host sends the seed at start.
- **Thread safety** — sockets live on background threads and push decoded
  messages into `queue.Queue`. Kivy widgets are only ever touched on the main
  thread, from the `Clock` loop.
- `net/` imports nothing from Kivy; `game/` (except `renderer.py`,
  `backdrop.py` and `game_input.py`) is pure Python and testable headless.

Every tunable — physics, rope constants, ports, timings, colours — lives in
`game/constants.py`.

### Who runs the clock

`GameHost` owns the `Simulation`, but deliberately does *not* own a clock: the
Kivy layer calls `host.tick(dt)` from its `Clock` callback. So the simulation
only ever advances on the main thread, nothing needs a lock around sim state,
and no socket thread can reach a widget. Socket threads do exactly two things —
decode frames into dicts, and put them on a `Queue`.

`main.py` runs the single pump that drains those queues and routes messages.
Centralising it there (rather than per-screen) means nothing is dropped during
a screen transition — which is exactly when `start` and `gameover` arrive.

Input is the one exception to the queue rule: `input` messages are written
straight into a plain dict on the host (`{player_id: (jump, duck)}`), latest
wins. A tuple assignment into a dict is atomic under the GIL, and it keeps a
60 Hz-per-client message stream out of the queue entirely.

### Two transports, on purpose

| | TCP `PORT_GAME` (50506) | UDP `PORT_GAME_UDP` (50507) |
|---|---|---|
| carries | join, lobby, ready, skin, `start`, `gameover`, `rematch`, crash events | `input`, `state` snapshots |
| rate | a handful per run | `INPUT_HZ` 60 up, `SNAPSHOT_HZ` 60 down |
| if a packet is lost | retransmit — it matters | skip it — a newer one is 16 ms away |

The split exists because of **head-of-line blocking**. TCP guarantees order, so
one lost snapshot stalls every snapshot queued behind it until the retransmit
lands about an RTT later. For a stream where each packet makes its predecessors
irrelevant, waiting is strictly worse than skipping: the game freezes in order
to redeliver a frame nobody wants any more. That is the lag spike.

Control messages are the exact opposite — rare, and each one unique — so they
stay on TCP, and the TCP connection doubles as the liveness signal.

Consequences worth knowing:

- **Every gameplay packet carries a `seq`.** Receivers keep `last_seq` and drop
  anything not newer (`protocol.SeqFilter`), so reordered and duplicated
  datagrams are discarded rather than rendered backwards. Nothing ever waits
  for a gap to be filled.
- **Input is resent at `INPUT_HZ`, not sent on change.** Over TCP a keypress
  was one reliable message; over UDP that dropped packet is a jump the host
  never hears about. Resending is the reliability mechanism, and it is safe
  because the host detects a rising edge per tick — a repeated `jump:true`
  cannot produce a second hop.
- **UDP is connectionless, so the host must be told where to reply.** On
  `start`, each client sends `udp_register` with its player id; the host
  records the source address and `sendto()`s snapshots there. That single
  datagram can be lost, so clients re-announce every `UDP_REGISTER_INTERVAL`
  until the first snapshot arrives. The claimed id is checked against the
  address that client's TCP connection came from, so a stray packet from
  another game on the same subnet cannot redirect someone's stream.
- **Crashes go over TCP, everything else rides in the snapshot.** Jump and
  land thuds are cosmetic enough to miss occasionally; a crash with no sound,
  no shake and no particles reads as a bug. The host strips crash events out
  of the snapshot and sends them reliably, so they can never fire twice.
- **Snapshots fit in one datagram.** Measured worst case — 4 players, 4
  obstacles, 3 power-ups, every effect active — is 936 bytes against a
  `MAX_DATAGRAM_BYTES` ceiling of 1200, so nothing fragments. Oversized
  packets fall back to TCP rather than being dropped.
- **`USE_UDP_GAMEPLAY`** flips the whole thing back to the all-TCP path, which
  is kept working. The same fallback engages per-client whenever an endpoint
  has not registered yet.

### Entity interpolation

Snapshots arrive 40 times a second at best and unevenly at worst; the screen
redraws 60+ times a second. Drawing the newest snapshot the instant it lands
means the world lurches on arrival and freezes in between.

So clients render the world at `now - INTERP_DELAY_MS` (100 ms). At any moment
there is almost always a snapshot on each side of the instant being drawn, so
positions are linearly interpolated between the two — players matched by `id`,
obstacles by `oid`, power-ups by `pid`. Anything present in only one of the
pair is taken as-is, which stops obstacles sliding in from the wrong place as
they spawn and despawn.

The delay is the entire trade-off: too low and a late packet leaves nothing to
interpolate toward; too high and everyone is visibly behind where they really
are. 100 ms absorbs four consecutive dropped snapshots at `SNAPSHOT_HZ`.
`SNAPSHOT_BUFFER` of 12 holds 300 ms of history, so the bracketing pair is
always still there.

**The local player is excluded** (`interpolate(..., skip_ids=)`). Interpolation
renders the past, and the past is the one place your own dino must never be —
you press jump and expect to leave the ground *now*. That player is drawn from
client-side prediction instead.

Measured under 10% packet loss and heavy arrival jitter: **0% frozen frames**,
blend factor sweeping the full 0→1, no backwards motion.

### Client-side prediction

Interpolation renders the past, which is right for everyone except you.
Pressing jump and watching your own dino leave the ground a round trip later
is the most noticeable flaw a networked action game can have — you *feel*
your own input in a way you never feel a partner being 100 ms behind.

So the local dino is simulated on the client the instant input is read
(`game/prediction.py`), and the host's later word arrives as a correction
rather than as news. Measured: **0 frames** from press to visible motion,
against ~6 frames (106 ms) unpredicted on a 6 ms LAN.

Two things make it accurate:

- **It reuses the host's own functions** — `apply_player_forces` then
  `integrate_and_clamp`, the same two calls `Simulation.step` makes, with the
  rope step between them simply skipped. Verified to reproduce the host's jump
  arc to **0.00e+00 px** over a full second. A reimplementation would drift
  the moment either function was touched.
- **It steps in fixed `TICK_DT` slices**, not by frame time. Gravity is
  integrated with Euler, whose result depends on `dt`, so stepping by a
  variable frame time traces a subtly different arc — a systematic drift that
  reconciliation would then fight every frame.

**The rope is deliberately not predicted.** It couples you to neighbours whose
inputs this device does not have; guessing them produces confident, wrong
motion that has to be yanked back. So being ripped off the floor by a partner
always arrives as a correction — which is exactly what reconciliation is for.

Corrections are eased in at `RECONCILE_LERP` (a fifth of the error per frame,
converging in ~10 frames) unless the error exceeds `RECONCILE_SNAP` (120 px,
over half a jump's peak), where easing would be dishonest and it snaps. A
77 px rope yank eases in at **15 px/frame worst case** — no teleporting.

#### Reconciling against a moving target

A snapshot describes the past: by the time it is decoded it is a packet's
flight plus up to a snapshot interval old. Reconciling against that raw value
drags the prediction toward a stale position every frame — measured at
**44.5 px behind the truth at the peak of a jump**, most of a dino's height,
enough to make you look like you cleared a cactus you actually hit.

So the sample is first rolled forward by its own age with the same integrator
(`LocalPredictor._catch_up`) before being compared. That drops the steady-state
offset to **0.0 px** and leaves reconciliation doing only what it should:
correcting what prediction genuinely could not know.

### Tuning, and how the numbers were chosen

Every constant here was picked from a measurement, not by feel. The rigs live
outside the repo, but they are all reproducible from `select_pair` and
`LocalPredictor`, which are pure and take no Kivy.

**Snapshot rate vs interpolation delay.** Swept over 20-second runs at 60 fps
against three synthetic networks. The surprise: raising the *rate* buys more
than raising the *delay*, because a dropped packet leaves a gap one interval
wide, and the delay only has to cover the worst gap — so a faster stream needs
a shorter delay to ride out the same loss.

| bad Wi-Fi (1.2× jitter, 10% loss) | stalls | mean lag |
|---|---|---|
| 40 Hz / 100 ms | 0.17% | 122 ms |
| **60 Hz / 66 ms** | **0.17%** | **81 ms** |
| 60 Hz / 50 ms | 0.59% | 65 ms |

Same smoothness, 41 ms closer to the truth. 50 ms is where it starts to break
down. Cost is ~165 KB/s upstream to three clients — nothing on a LAN.

**`RECONCILE_LERP`.** Measured against a 77 px rope yank at 60 fps. 0.10 leaves
your dino drawn in the wrong place for 417 ms; 0.30 moves it a third of a body
height in one frame. 0.20 is the knee: 15 px worst step, settled in 200 ms.

**Packet loss.** `SIMULATED_PACKET_LOSS` (ships at 0.0) drops inbound gameplay
datagrams on purpose in the client's receive thread. Measured through real
sockets, tracking a scrolling obstacle — the dominant motion on screen, and
interpolated rather than predicted:

| induced loss | frames held | frames lurching |
|---|---|---|
| 0% | 0.9% | **0%** |
| 5% | 1.1% | **0%** |
| 10% | 0.0% | **0%** |
| 30% | 2.2% | **0%** |

The lurch count is the one that matters: a lurch means the view gave up
interpolating and snapped, which is the TCP-era stutter. It stays at zero even
at 30% loss — triple anything realistic — where the stream simply holds a
frame now and then. Losing packets costs smoothness, never time.

*(Measuring this correctly took two attempts. The first version tracked a
dino's height and reported ~44% "frozen" frames at every loss level — a number
completely insensitive to the variable under test, which is the tell. It was
counting a dino standing still on the ground as a stutter.)*

### One clock, and why it is `perf_counter`

Everything that measures elapsed time goes through `game/timing.py`, which is
`time.perf_counter` — never `time.monotonic`.

The two look interchangeable. On Windows (CPython ≤ 3.12) `monotonic` is
`GetTickCount64`, whose resolution is the scheduler tick, about **15.6 ms**;
measured here it did not advance once across 2000 consecutive calls, while
`perf_counter` resolved to 0.1 µs.

That is invisible for a six-second timeout and ruinous at frame scale. With
interpolation on a 15.6 ms clock, both the render target and the snapshot
arrival stamps snapped to the same grid, so the blend factor jumped in steps
instead of sweeping — **a third of rendered frames showed no movement at all**,
precisely the stutter interpolation exists to remove. The same granularity
made a 1–3 ms LAN round trip read as either 0 ms or 15.6 ms, so the
connection-quality HUD was meaningless.

The epoch is arbitrary and differs from `monotonic`'s, so the rule is
consistency: every value that gets compared or subtracted must come from
`timing.now()`. Mixing the two silently breaks both timeouts and interpolation.

## Characters

Six creatures, picked in the lobby and carried in the `join`/`skin` messages:

| | | |
|---|---|---|
| **Rex** — classic dino, heavy head and tail | **Raptor** — lean, crested, long stride | **Stego** — low slung, back plates |
| **Ptero** — swept wings, beak and crest | **Croc** — long snout, ridged back | **Yeti** — round and shaggy |

They are **purely cosmetic**, and that is enforced rather than assumed. Every
character is defined in `game/constants.py` as parts in coordinates normalised
to the hitbox, so all six occupy exactly the same `PLAYER_WIDTH x PLAYER_HEIGHT`
box. Picking a bulkier-looking creature cannot hand the team a worse hitbox —
which matters here, because one player's hitbox kills everybody. There are
tests asserting identical standing and ducking hitboxes across all six, and
that two runs with different characters produce byte-identical simulations.

Ducking needs no separate art: the hitbox itself shrinks to
`PLAYER_DUCK_HEIGHT`, so the whole silhouette squashes with it.

To add a character, append one entry to `SKINS` — a name, a colour, a list of
parts (`rect`, `ellipse` or `tri`) and an eye position. Nothing else changes.

## Seasons

The background is eight painted seasons that the team runs *through*, from
`assets/Dino-Stick BG pics/`. Each season holds for **500 m**, tiling
seamlessly as it scrolls right to left, and then a transition image — the ones
with a `T` in the name — makes a **single** pass to hand over to the next:

```
1-Start  →1T2→  2-Rainy  →2T3→  3-Snowy  →3T4→  4-Mid  →4T5→
5-Desert →5T6→  6-Astroid →6T7→ 7-Ashy   →7T8→  8-Grassland →8T1→ (wraps)
```

That is 4,800 m of scenery per lap: 8 × 500 m of season plus 8 × 100 m of
transition. Nothing fades or cuts between them — the transition art already
paints one season into the next, so scrolling through it *is* the transition.

`game/backdrop.py` lays all of it out as one endless strip and reads from it at
whatever point the team has reached. The layout is a **pure function of
distance**, which is what makes the progression work identically in all three
modes for nothing: local, host and client all know `state.distance` already, so
all three land on the same season at the same metre mark without a byte of it
going on the wire. There is no scroll accumulator to drift, nothing to reset
between runs, and a mid-run window resize simply re-lays the strip in the right
place instead of tearing.

Three details that are less obvious than they look:

- **Seasons snap to whole copies.** The number of copies in 500 m is rounded,
  and the effective parallax absorbs the few percent of error. Spacing them at
  an exact `BG_PARALLAX` instead leaves the last copy cut off mid-image where
  the transition begins, which reads as a tear across the sky.
- **Every image is dropped onto the ground line individually.** The painted
  ground sits at a different height in each one (`BG_GROUND_FRACTIONS`,
  measured off the art — the rainy waterline is a good 5% of the image lower
  than the jungle's grass). One shared value left a visible step in the ground
  at every seam. The *height* is still shared, or the world would zoom in and
  out by a third from one season to the next.
- **The renderer scrolls on its own smoothed metre count.** A client's distance
  arrives in ~20 Hz steps, and scenery moving at snapshot rate next to
  obstacles interpolated every frame reads as a judder. So it advances locally
  at the world speed and eases onto the authoritative figure, jumping only when
  the gap is too big to be lag (a new run, or joining one in progress).

Loading is asynchronous, lazy and capped at `BG_CACHE` textures, with the next
image warmed `BG_PREFETCH_METRES` ahead. A missing or still-decoding image is
simply not drawn and the renderer falls back to the old parallax hills for that
frame, so a build without the art still runs.

## UI

The UI is built for a phone held in landscape, which is a surprisingly tight
box: wide, but only about **390dp of height** (1080px at ~2.75 density).

The look lives entirely in `ui/theme.py` (colours, type scale, metrics) and
`ui/widgets.py` (buttons, cards, badges, stats, meters, dialogs) — flat
near-white surfaces, rounded corners, one green accent, all drawn on the
canvas with no images or `.kv` files. Screens compose those widgets and never
name a colour themselves.

Two rules keep the four screens coherent:

- **Colour carries meaning, never decoration.** Green = go, red = you died or
  the rope is about to snap, amber = warning, grey = information.
- **One primary (filled) button per screen.** The menu used to be four
  identical green bars with no way to tell which one you wanted.

### Numbers players can read

The sim works in pixels; the HUD does not. Everything shown to a human goes
through `ui/format.py` and comes out in metres, km/h and grouped digits
(`PIXELS_PER_METRE = 100`, which makes the dino 0.6m tall, a synced jump 2m,
and the run accelerate from 15km/h to 40km/h). The old HUD read
`62,043 px   890 px/s` — a debug line: nobody knows whether 62,043px is a good
run. Game over shows score, distance, run time and team size as labelled
stats rather than one bare number.

### Phone-first details

- **Everything is sized in dp/sp**, never raw pixels. A 56-pixel button is fine
  in a 1280x720 desktop window and a fingernail-sized sliver on a 1080p phone,
  because the phone packs 2–3x the pixels into the same physical inch. On
  desktop `dp()` is 1:1, so the desktop build is unchanged.
- **Touch targets are ≥48dp**, Android's recommended minimum; menu buttons are
  56dp.
- **Buttons have a pressed colour.** Touch has no hover, so without one a tap
  gives no feedback and the control feels dead.
- **Menus are a centred column** capped at 520dp. Full-bleed on a 20:9 screen
  would stretch one button into a 2000px bar.
- **Panels scroll** if content outgrows the screen, so nothing is ever cut off
  on a short or unusual display.
- **Hidden controls collapse to zero height** rather than just going
  transparent, which otherwise leaves dead gaps in the lobby.
- **Back button** steps back through the app instead of killing it — on
  Android the default would silently drop a hosted game and everyone on it.
- **The screen is kept awake** during play; a runner goes long stretches with
  no touch input, so the display would otherwise dim mid-run.
- **The IP box asks for the numeric keypad** (`input_type="number"`).
- **The join dialog gives up gracefully**: after 6 seconds with no games found
  it points at manual IP entry rather than spinning forever.
- **The host's address gets its own card** in the lobby, big enough to read
  out across a room — it is the one thing a host has to tell everybody.
- **The control hint fades** after 8 seconds instead of sitting across the
  bottom of the screen for the whole run.
- **The soft keyboard pans the window** (`Window.softinput_mode`). Android
  draws it *over* your app by default, and in landscape it covers most of the
  screen — both text fields were invisible while being typed into.
- **The address field asks for the text keyboard**, not the numeric one,
  however wrong that looks: Android's number pad has no `.` key on most
  keyboards, which made the manual-join fallback untypeable.
- **Your name and dino are remembered.** Without a name, every host announced
  itself as "Player 1's game" and every lobby row read "Player 1".

## Touch controls

On a phone the screen splits in half: **right half = jump, left half = duck**,
both held actions, so two thumbs work independently. The top 16%
(`TOUCH_DEAD_ZONE_TOP`) is a dead zone
so tapping *Quit* does not also jump. Remap by editing `TOUCH_ZONES` in
`game/constants.py` — nothing else needs to change.

Keyboard stays active everywhere, and the two are OR-ed, so a Bluetooth
keyboard on a tablet does not fight the on-screen zones.

## Build the APK

Buildozer does not run on Windows; use WSL, a Linux box, or a VM. `buildozer.spec`
is already written — do **not** run `buildozer init`, it would overwrite it.

```bash
# In WSL/Ubuntu. These need sudo and will prompt for your password.
sudo apt update
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip autoconf libtool \
                    pkg-config zlib1g-dev libncurses-dev cmake libffi-dev libssl-dev
pip3 install --user --upgrade buildozer cython==0.29.36

cd /mnt/c/Users/<you>/Desktop/Dino\ Stick/Dino-Stick-/dinostick
buildozer -v android debug          # first run downloads ~5GB of SDK/NDK
```

The APK lands in `bin/`. Install and run it with:

```bash
buildozer android deploy run logcat
```

### Android gotcha: the multicast lock

Android drops broadcast/multicast packets before they reach a socket unless the
app holds a `WifiManager.MulticastLock`. `net/discovery.py` acquires one via
pyjnius while listening, and `CHANGE_WIFI_MULTICAST_STATE` is declared in the
spec — without both, discovery is *silently* dead: the socket binds fine and
simply never receives anything.

It degrades safely. If the lock cannot be taken (vendor quirks, missing
permission, pyjnius unavailable) the game still runs, and **manual IP entry in
the join dialog always works** — the host's address is shown in its own lobby.

### Testing on two phones

1. Build and install on both.
2. One phone: **Host Game**; the lobby shows the address in a card.
3. Other phone: **Join Game** — the host appears in the list within a second;
   tap it. If the list stays empty, type the address from step 2.
4. Guest phone taps **I'm Ready**, host taps **Start Game**.

Both on the same Wi-Fi or one phone's hotspot. No internet needed.

Connecting is deliberately *not* automatic — you pick the game you meant to
join. What is automatic is finding it.

**If the list stays empty**, in order of likelihood:

- The other phone is not hosting yet. Only the joiner scans, and only while
  the join dialog is open.
- The network blocks device-to-device traffic. "AP isolation" is on by default
  on a lot of guest and public Wi-Fi, and it blocks the game connection too,
  not just discovery — a phone hotspot is the reliable fallback.
- Broadcast specifically is blocked. Manual entry still works.

### Surviving a real network

Two phones on Wi-Fi disconnect in ways a LAN cable does not, and TCP will not
tell you about most of them — a phone that walks out of range leaves a socket
that looks perfectly healthy for minutes.

- **Both ends keepalive.** Clients ping once a second in *every* screen, and
  either end drops the peer after `CONNECTION_TIMEOUT` of silence. Without it
  the host broadcast into the void while that player's dino stood still and
  killed the team.
- **Announcements are aimed, not just broadcast.** `255.255.255.255` follows
  the *default* route, so a handset with mobile data up announced itself over
  cellular where nobody was listening. The announcer binds to the Wi-Fi
  interface and also sends to the `/24`-directed address.
- **The local address survives having no internet.** Probing `8.8.8.8` for the
  outbound interface fails on a hotspot phone with mobile data off — no default
  route — and the lobby advertised `127.0.0.1`. It now falls back to probing
  the usual hotspot and router subnets.
- **Preferences persist** (`ui/settings.py`) in the app's private directory,
  written on every change: Android kills backgrounded apps without running
  `on_stop`.
