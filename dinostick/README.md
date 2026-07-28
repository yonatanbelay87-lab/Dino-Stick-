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

| | occupies | standing (0–81) | ducking (0–42) | jump over it? |
|---|---|---|---|---|
| `BIRD_LOW` | 26–60 | hit | hit | **must jump** (clears at 60) |
| `BIRD_HIGH` | 46–86 | hit | safe | solo jump (83) fails, synced (196) clears |

Ducking needs both feet on the ground — so a bird arriving while the rope has
you airborne is lethal. That is the point.

`BIRD_HIGH`'s underside at 46 is what caps how big the characters can get: the
ducking box has to stay below it or ducking stops being an answer. At 42 there
is 4px of daylight left.

Power-ups are team-wide and float at 70–160px, above a solo jump, so
collecting the good ones is itself a reason to coordinate. Shield absorbs one
fatal hit for everyone; Slow-mo, Feather (low gravity + slack rope) and Sync
(rope fully slack) are timed; Star is a score bonus.

Each one carries an **icon** — shield crest, hourglass, up arrow, two
interlocking links, star — because colour alone only works once you have
memorised the palette. They are drawn from `POWERUP_ICONS`, in the same
normalised-parts idiom as the characters, so an icon cannot escape its disc
and the set rescales with `POWERUP_SIZE`. The ink is picked per disc by
brightness, which is what stops the yellow Star getting a white icon nobody
can read.

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

- **Peer-authoritative co-op.** Every device fully simulates its *own* dino from
  local input and nothing else's. Input moves your dino on the frame you pressed
  it -- nothing is sent first and no reply is waited for. Partners arrive as a
  20 Hz stream of 15-byte snapshots and are rendered 100 ms in the past,
  interpolated. There is no reconciliation and no lag compensation, because
  there is no server to disagree with you.
- **The world is the seed.** Obstacles, power-ups and the difficulty ramp are a
  pure function of (seed, tick), computed identically on both devices. No
  obstacle data has ever been on the wire.
- **Fixed 60 Hz timestep** via an accumulator, decoupled from render framerate.
  The network tick is separate again, at 20 Hz.
- **Thread safety** -- sockets live on background daemon threads and push into a
  `queue.Queue` (reliable) or a `deque` (state stream). Kivy widgets are only
  ever touched on the main thread, from the `Clock` loop. No blocking socket
  call ever runs on the Kivy thread.
- `net/` imports nothing from Kivy; `game/` (except `renderer.py`,
  `backdrop.py` and `game_input.py`) is pure Python and testable headless.

Every tunable -- physics, rope constants, ports, timings, colours -- lives in
`game/constants.py`, except the ones belonging to the unreliable path, which
live at the top of `net/statepacket.py` next to the code that packs the packets.

**The netcode has its own document: [NETCODE.md](NETCODE.md).** It covers the
authority model, both transports, the deterministic world, how to tune
`INTERP_DELAY` for a higher-latency connection, and two measured findings that
are load-bearing and counter-intuitive. Run `python tools/net_selftest.py` from the
repo root to check the whole thing headlessly.

This replaced a host-authoritative design in which the host simulated everyone
and broadcast the entire world 60 times a second, with client-side prediction
and reconciliation on top. It worked, but a joiner's collisions were still ruled
on by the host, so a hit registered a round trip late and prediction then had to
snap the dino back. That snap was the rubber-banding.


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

The box is **60 × 81** — scaled up about 35% from the original 44 × 60 when the
painted seasons went in, because a 60px dino that read fine against a plain
background read as an ant beside a 200px palm tree. Because the art is
normalised, that was three numbers and no art. Standing height is nearly free
(a grounded dino already overlaps everything that can reach it, and clearing an
obstacle is decided by where its feet are); the width costs a little timing —
worst case `BIRD_HIGH`, where the leeway on a solo jump goes from 0.501s to
0.487s at `MAX_SPEED`, a 2.9% trim.

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

Buildozer does not run on Windows, so the build happens in CI. `buildozer.spec`
is already written — do **not** run `buildozer init`, it would overwrite it.

### The normal way: GitHub Actions

`.github/workflows/android.yml` builds a debug APK on every push that touches
`dinostick/`, and on demand from the **Actions** tab → *Android APK* → *Run
workflow*. When it finishes, the APK is on the run's summary page under
**Artifacts** → `dinostick-debug-apk`.

To install it: unzip the artifact, copy the `.apk` to the phone, and open it.
Android will ask you to allow installs from whatever app you opened it with —
that is expected for a debug build, which is signed with a throwaway key rather
than a real one.

A cold run takes 30–45 minutes (it downloads and unpacks the whole Android
SDK/NDK); after that the cache brings it down to a few minutes. Buildozer is
pinned to **1.6.0** because adaptive launcher icons are silently ignored on
1.5.0 — see the note in `buildozer.spec`.

The workflow also unpacks the finished APK and asserts all 16 season images are
actually inside it. That check exists because a missing backdrop does not crash
the game — it quietly falls back to the grey hills, which is easy to miss.

### Locally, if you want fast iteration

Needs WSL, a Linux box or a VM, and roughly 5GB of SDK/NDK on first run:

```bash
sudo apt update
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip autoconf libtool \
                    pkg-config zlib1g-dev libncurses-dev cmake libffi-dev libssl-dev
pip3 install --user --upgrade buildozer==1.6.0 cython==0.29.36

cd /mnt/c/Users/<you>/Desktop/Dino\ Stick/Dino-Stick-/dinostick
buildozer -v android debug
```

The APK lands in `bin/`. With a phone plugged in and USB debugging on:

```bash
buildozer android deploy run logcat
```

### Branding

The launcher icon and splash are **generated**, not hand-drawn:

```bash
python tools/make_branding.py
```

It draws the dino straight from `SKINS` and crops the backdrop from the season
the game opens on, writing `icon.png` (legacy), `icon_fg.png` / `icon_bg.png`
(the two adaptive layers Android 8+ masks into the launcher's shape) and
`presplash.png`. Re-run it after changing a character or the first season's
art, so the icon cannot drift from the game. `android.presplash_color` is
sampled from the splash image's own edge columns — that colour fills the bars
either side of it on a landscape phone.

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
