# Dino Stick

A local-multiplayer co-op endless runner. 2–4 dinos, all tied together by an
elastic rope. The rope exerts real spring forces, so jumping out of sync drags
your teammates. If any one player crashes, **everyone** loses.

LAN only — UDP broadcast for discovery, direct TCP for game traffic. No
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
ground clamp so a partner's tug can rip a grounded dino off the floor. Tuned
against a measured response curve rather than by eye:

| desync between two jumps | apexes reached (a free jump is 188px) | |
|---|---|---|
| 0 ms | 196 / 196 | rope never engages |
| 50 ms | 167 / 212 | slight tug, partner slingshotted |
| 100 ms | 173 / 174 | still clears a large cactus |
| **167 ms** | **83 / 77** | **cliff — neither clears a large cactus** |
| solo jump | 83 / 77 | partner dragged 77px into the air |

Past ~167ms the late player has already been yanked airborne, so their jump
input is silently eaten — you cannot jump when you are not grounded. That is
the punish. By ~500ms they have landed and can act again, which is what keeps
it recoverable.

`MAX_STRETCH` is 40px, far below the 220 in the original design sketch, and it
has to be: two dinos can never be more than one jump height apart (~196px), so
a 220px limit is physically unreachable and the stiffening branch would be dead
code.

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
- `net/` imports nothing from Kivy; `game/` (except `renderer.py` and
  `game_input.py`) is pure Python and testable headless.

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
