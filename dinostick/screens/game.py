"""Game screen: fixed-timestep loop, renderer, HUD, input wiring.

Three modes share this screen:

  local   -- two dinos on one keyboard, simulated here (Phase 2 behaviour)
  host    -- the authoritative sim runs here, driven by our Clock, and is
             broadcast to everyone
  client  -- no simulation at all: send input, render the host's snapshots
             interpolated between the two most recent
"""

from __future__ import annotations

import random

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.widget import Widget
from kivy.utils import platform

from game import constants as C
from game import timing
from game.entities import interpolate, select_pair
from game.game_input import InputMapper
from game.physics import rope_tension
from game.renderer import Renderer
from game.prediction import LocalPredictor
from game.simulation import Simulation, make_players, modifiers_for
from ui import audio, theme
from ui import format as fmt
from ui.insets import insets
from ui.widgets import Badge, Card, ConfirmDialog, Meter, Stat, TouchButton

from . import GAMEOVER, MENU

# Dinos driven from this one keyboard in local co-op.
LOCAL_PLAYERS = 2

# How long the control reminder stays up before fading. It is for the first
# ten seconds of your first run; after that it is clutter across the bottom of
# a screen that is already busy.
HINT_SECONDS = 8.0


class GameView(Widget):
    """Bare canvas the Renderer draws into, and the touch surface.

    Touches are forwarded to the InputMapper, which decides which zone they
    fall in. Returning True consumes the touch so it does not fall through.
    """

    mapper = None  # set by GameScreen for the life of a run

    def on_touch_down(self, touch):
        if self.mapper is not None and self.collide_point(*touch.pos):

            
            if self.mapper.touch_down(touch.uid, touch.x - self.x,
                                      touch.y - self.y, self.width, self.height):
                return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if self.mapper is not None:
            if self.mapper.touch_move(touch.uid, touch.x - self.x,
                                      touch.y - self.y, self.width, self.height):
                return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if self.mapper is not None and self.mapper.touch_up(touch.uid):
            return True
        return super().on_touch_up(touch)



class GameScreen(Screen):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.sim: Simulation | None = None
        self.mapper: InputMapper | None = None
        self._event = None
        self._accumulator = 0.0
        self._mode = "local"
        self._last_event_tick = -1
        self._dead_zone_top = theme.TOUCH_MIN * 2.0
        self._exit_dialog = None
        # Client mode only: runs this device's own dino ahead of the network.
        self._predictor = LocalPredictor()
        audio.preload()

        root = FloatLayout()

        # The world is drawn edge to edge -- the painted seasons should reach
        # into the notch, they are scenery. Only the HUD below is inset.
        self.view = GameView()
        root.add_widget(self.view)
        self.renderer = Renderer(self.view)

        # Score card, top right. On a card rather than bare text because the
        # HUD floats over rolling hills and parallax -- grey-on-grey text
        # disappeared exactly when a run got interesting.
        #
        # Distance and speed are in metres and km/h. They used to read
        # "62,043 px   890 px/s", which is a debug line: nobody knows whether
        # 62,043 px is a good run.
        #
        # auto_height, not a hard dp(84): the card's contents are sized in sp,
        # so a player with Android's font size turned up had the distance line
        # clipped off the bottom of a card that could not grow.
        self.score_card = Card(
            size_hint=(None, None),
            fill=(1.0, 1.0, 1.0, 0.88), outline=(0, 0, 0, 0.08),
            auto_height=True, padding=(theme.SPACE_3, theme.SPACE_2),
            spacing=0)
        self.score_stat = Stat("Score", "0", value_size=theme.FONT_HEADING)
        self.score_card.add_widget(self.score_stat)
        self.progress_label = Label(
            text="", font_size=theme.FONT_SMALL, color=theme.GROUND,
            size_hint_y=None, height=theme.FONT_SMALL * 1.6)
        self.score_card.add_widget(self.progress_label)
        root.add_widget(self.score_card)

        # Rope tension: how close the team is to a hard yank. Centred at the
        # top, clear of both thumbs in landscape. The bar changes colour with
        # the value, so it is readable without looking straight at it.
        self.tension_label = Label(
            text="ROPE", font_size=theme.FONT_CAPTION, color=theme.FAINT,
            size_hint=(None, None),
            size=(dp(80), theme.FONT_CAPTION * 1.6))
        root.add_widget(self.tension_label)
        self.tension_meter = Meter(size_hint=(None, None),
                                   size=(dp(160), dp(10)))
        root.add_widget(self.tension_meter)

        # Active team power-ups, as badges in the team's colours instead of a
        # run-on string. They sit under the rope meter: dead centre of the
        # sky, where nothing else is ever drawn.
        #
        # Width is assigned in _layout_hud from the safe box. It was a fixed
        # dp(360), which is wider than the whole safe area on a small phone.
        self.effects_row = BoxLayout(
            orientation="horizontal", spacing=theme.SPACE_1,
            size_hint=(None, None), size=(dp(360), dp(26)))
        root.add_widget(self.effects_row)
        self._effect_badges: dict[str, Badge] = {}
        self._effect_kinds: list[str] = []

        # Connection quality (client) / role (host).
        self.net_label = Label(
            text="", font_size=theme.FONT_SMALL, color=theme.GROUND,
            halign="left", valign="middle",
            size_hint=(None, None), size=(dp(240), dp(24)))
        self.net_label.bind(size=lambda w, *_: setattr(
            w, "text_size", (w.width, w.height)))
        root.add_widget(self.net_label)

        # One nameplate per dino, repositioned every frame by the renderer, so
        # these are the one HUD element the safe box does not govern -- they
        # follow the world, and the world is full-bleed.
        self.nameplates: list[Label] = []
        for _ in range(C.MAX_PLAYERS):
            plate = Label(text="", font_size=theme.FONT_TINY,
                          size_hint=(None, None), size=(dp(120), dp(20)),
                          opacity=0.0)
            self.nameplates.append(plate)
            root.add_widget(plate)

        self.hint_label = Label(
            text="", font_size=theme.FONT_SMALL, color=theme.FAINT,
            halign="center", valign="middle",
            size_hint=(None, None), size=(dp(620), dp(24)))
        self.hint_label.bind(size=lambda w, *_: setattr(
            w, "text_size", (w.width, w.height)))
        root.add_widget(self.hint_label)

        # Big touch-zone labels, shown for the first few seconds of a run so a
        # new player on a phone knows which half does what, then faded away.
        self.zone_hints: list[Label] = []
        for text, sub in (("DUCK", "hold this half"), ("JUMP", "tap this half")):
            hint = Label(text=f"{text}\n[size={int(theme.FONT_SMALL)}]{sub}"
                              f"[/size]",
                         markup=True, halign="center", valign="middle",
                         font_size=theme.FONT_TITLE,
                         color=theme.FAINT, opacity=0.0,
                         size_hint=(None, None), size=(dp(240), dp(88)))
            self.zone_hints.append(hint)
            root.add_widget(hint)

        # Thumb-sized pause target. It sits inside the input dead zone at the
        # top of the screen, so pressing it can never also trigger a jump.
        # Plain text on purpose: block/pause glyphs are not in Kivy's default
        # font and render as tofu boxes on both desktop and Android.
        self.quit_button = TouchButton(
            "Pause", self.request_exit,
            variant="overlay",
            height=theme.TOUCH_MIN,
            size_hint=(None, None),
        )
        self.quit_button.width = dp(104)
        self.quit_button.font_size = theme.FONT_SMALL
        root.add_widget(self.quit_button)

        self.root_layout = root
        self.add_widget(root)

        root.bind(pos=self._layout_hud, size=self._layout_hud)
        # The score card sizes itself to its text, so it has to be re-anchored
        # to the top-right corner every time that height changes -- otherwise
        # it grows downward from a stale y and drifts off the corner.
        self.score_card.bind(height=self._layout_hud)
        insets.bind_layout(self._layout_hud)
        self._layout_hud()

    # -- HUD layout ---------------------------------------------------------

    def _layout_hud(self, *_args) -> None:
        """Place every HUD element against the SAFE box, in dp from its edge.

        Everything here used to be a pos_hint fraction of the window, which is
        two bugs in one. A fraction is not a distance -- ``x: 0.015`` is 13dp
        on a phone and 30dp on a tablet -- and a fraction of the *window* knows
        nothing about the camera cutout, so in landscape the Pause button sat
        under the notch and the control hint ran under the gesture bar.
        """
        root = self.root_layout
        sx, sy, sw, sh = insets.box(root.width, root.height)
        if sw <= 0 or sh <= 0:
            return
        sx, sy = root.x + sx, root.y + sy
        edge = theme.EDGE
        top = sy + sh - edge
        centre_x = sx + sw * 0.5

        # Top-left: pause. Top-right: score.
        self.quit_button.x = sx + edge
        self.quit_button.top = top

        self.score_card.width = min(dp(180), max(dp(132), sw * 0.34))
        self.score_card.right = sx + sw - edge
        self.score_card.top = top

        # Top-centre: the rope meter, between the two of them.
        self.tension_label.center_x = centre_x
        self.tension_label.top = top
        self.tension_meter.width = min(dp(160), sw * 0.30)
        self.tension_meter.center_x = centre_x
        self.tension_meter.top = self.tension_label.y - theme.SPACE_1

        # Power-up badges hang under the meter, clear of the score card.
        self.effects_row.width = max(dp(120), sw - 2 * (edge + dp(112)))
        self.effects_row.center_x = centre_x
        self.effects_row.top = self.tension_meter.y - theme.SPACE_3

        self.net_label.x = sx + edge
        self.net_label.top = self.quit_button.y - theme.SPACE_2

        # Bottom: the control reminder, above the gesture bar rather than
        # under it.
        self.hint_label.width = max(dp(200), sw - 2 * edge)
        self.hint_label.center_x = centre_x
        self.hint_label.y = sy + edge

        # The zone hints label the touch halves, so they follow the halves of
        # the SAFE box -- otherwise they drift off-centre from the zones they
        # are describing on a phone with a cutout down one side.
        for index, hint in enumerate(self.zone_hints):
            hint.width = min(dp(240), sw * 0.4)
            hint.center_x = sx + sw * (0.25 if index == 0 else 0.75)
            hint.y = sy + sh * 0.28

        self._sync_dead_zone()

    def _sync_dead_zone(self) -> None:
        """Reserve the top strip so tapping Pause cannot also jump.

        Measured in dp down from the top of the screen, not as a fraction of
        it. The old 16% was 58dp on a small phone (barely clearing a 48dp
        button) and 128dp on a tablet (a fifth of the play area thrown away for
        a control that had not grown at all).
        """
        self._dead_zone_top = (insets.top + theme.EDGE + theme.TOUCH_MIN
                               + theme.SPACE_2)
        if self.mapper is not None:
            self.mapper.dead_zone_top = self._dead_zone_top

    # -- lifecycle ----------------------------------------------------------

    def on_enter(self, *_args) -> None:
        self.start_run()

    def on_leave(self, *_args) -> None:
        # A networked run can end while the "leave?" question is still up --
        # the sim never stopped. Take the modal with us, or it hangs over the
        # game-over card with nothing behind it.
        if self._exit_dialog is not None:
            dialog, self._exit_dialog = self._exit_dialog, None
            dialog.dismiss()
        self._teardown()

    def start_run(self, seed: int | None = None) -> None:
        self._teardown()
        app = App.get_running_app()
        self._mode = app.mode if app else "local"
        # Last run's screen shake, particles and season are not this run's.
        self.renderer.reset()

        touch_device = platform in ("android", "ios")

        if self._mode == "local":
            # One dino on a touch device. Touch only ever drives local player
            # 0, so a second local dino would stand still and kill the team on
            # the first cactus -- a solo run on a phone was unwinnable.
            count = 1 if touch_device else LOCAL_PLAYERS
            # Your chosen name and dino carry into a local run, so the
            # nameplate over your own head is not a generic "P1".
            name = app.player_name if app else "Player 1"
            skin = app.skin if app else 0
            self.sim = Simulation(
                random.randrange(1 << 31) if seed is None else seed,
                make_players(count,
                             names=[name, "Player 2"][:count],
                             skins=[skin, (skin + 1) % len(C.SKIN_NAMES)][:count]))
            self.mapper = InputMapper(count)
            self.hint_label.text = (
                "Right half jumps      left half ducks" if touch_device else
                "P1:  SPACE / W = jump,  S = duck        "
                "P2:  UP = jump,  DOWN = duck")
        else:
            # Host and client both drive exactly one local dino.
            self.sim = None
            self.mapper = InputMapper(1)
            self.hint_label.text = (
                "Right half jumps      left half ducks" if touch_device else
                "SPACE / W = jump      S = duck")

        self._show_zone_hints(touch_device)
        self._fade_hint()

        self.mapper.bind_keyboard(Window)
        self.mapper.dead_zone_top = self._dead_zone_top
        self.view.mapper = self.mapper  # touch zones -> local player 0
        self._accumulator = 0.0
        self._last_event_tick = -1
        self._event = Clock.schedule_interval(self._frame, 0)

    def _fade_hint(self) -> None:
        """Show the control reminder, then get it out of the way."""
        Animation.cancel_all(self.hint_label)
        self.hint_label.opacity = 1.0
        (Animation(opacity=1.0, duration=HINT_SECONDS)
         + Animation(opacity=0.0, duration=1.2)).start(self.hint_label)

    def _show_zone_hints(self, touch_device: bool) -> None:
        """Flash DUCK / JUMP over their halves at the start of a touch run."""
        for hint in self.zone_hints:
            Animation.cancel_all(hint)
            if not touch_device:
                hint.opacity = 0.0
                continue
            hint.opacity = 0.0
            (Animation(opacity=0.55, duration=0.35)
             + Animation(opacity=0.55, duration=C.TOUCH_HINT_SECONDS)
             + Animation(opacity=0.0, duration=0.8)).start(hint)

    def _teardown(self) -> None:
        if self._event is not None:
            self._event.cancel()
            self._event = None
        if self.mapper is not None:
            self.mapper.unbind_keyboard()
            self.mapper = None
        self.view.mapper = None
        # Never carry a prediction across runs: a rematch reseeds the world,
        # and last run's dino would be blended into the new one.
        self._predictor.stop()

    # -- the loop -----------------------------------------------------------

    def _frame(self, frame_dt: float) -> None:
        app = App.get_running_app()
        if self._mode == "client":
            self._frame_client(app, frame_dt)
        elif self._mode == "host":
            self._frame_host(app, frame_dt)
        else:
            self._frame_local(frame_dt)

    def _frame_local(self, frame_dt: float) -> None:
        """Simulate locally with a fixed-timestep accumulator.

        Kivy calls this once per drawn frame with a variable delta. Physics
        must not vary with framerate (the rope is a spring, and springs
        integrated at a wobbly dt explode), so real elapsed time goes into an
        accumulator and the sim advances in whole TICK_DT slices. Leftover time
        stays banked for next frame.
        """
        if self.sim is None:
            return
        if self.mapper is not None:
            for index, state in enumerate(self.mapper.states):
                self.sim.set_input(index, state.jump, state.duck)

        self._accumulator += min(frame_dt, C.MAX_FRAME_DT)
        ticks = 0
        while self._accumulator >= C.TICK_DT and ticks < C.MAX_TICKS_PER_FRAME:
            self.sim.step(C.TICK_DT)
            self._accumulator -= C.TICK_DT
            ticks += 1
            if not self.sim.state.running:
                break
        # Hit the catch-up ceiling: the app was stalled. Drop the backlog
        # rather than spiralling further behind every frame.
        if ticks == C.MAX_TICKS_PER_FRAME:
            self._accumulator = 0.0

        self._present(self.sim.state, frame_dt)
        if not self.sim.state.running:
            self._end_local_run()

    def _frame_host(self, app, frame_dt: float) -> None:
        """The host simulates for everyone; its accumulator lives in GameHost."""
        host = app.host if app else None
        if host is None:
            return
        if self.mapper is not None:
            local = self.mapper.states[0]
            host.set_local_input(local.jump, local.duck)

        was_running = host.running
        host.tick(frame_dt)

        if host.sim is not None:
            self._present(host.sim.state, frame_dt)

        if was_running and not host.running and host.sim is not None:
            state = host.sim.state
            self._teardown()
            over = self.manager.get_screen(GAMEOVER)
            over.show_result(
                score=state.score, distance=state.distance,
                cause_player_id=state.cause_player_id,
                cause_obstacle=state.cause_obstacle,
                players=len(state.players),
                seconds=state.tick * C.TICK_DT,
            )
            self.manager.current = GAMEOVER

    def _frame_client(self, app, frame_dt: float) -> None:
        """Render the host's world, interpolated between two snapshots.

        We deliberately render INTERP_DELAY in the past. That way there
        is virtually always a newer snapshot to interpolate *toward*; rendering
        at the newest packet instead would leave the world frozen every time
        one arrived late.
        """
        if app is None or app.client is None:
            return

        if self.mapper is not None:
            local = self.mapper.states[0]
            # Every frame now, not just on change: over UDP the resend IS the
            # reliability mechanism (client.send_input rate-limits to
            # INPUT_HZ). The tick we last rendered rides along so the host can
            # tell how far behind us this input was formed.
            app.client.send_input(local.jump, local.duck,
                                  tick=self._last_event_tick)
            # The same input drives prediction immediately -- that is the
            # whole point: the jump starts on this frame, not when the host's
            # answer gets back.
            self._predictor.set_input(local.jump, local.duck)
        app.client.maybe_ping()

        snapshots = app.snapshots
        if not snapshots:
            return

        # Fire each snapshot's events exactly once, as it arrives. Interpolated
        # frames carry no events, so a crash cannot be replayed every frame
        # while the view catches up to it.
        pending: list[dict] = []
        for _, snap in snapshots:
            if snap.tick > self._last_event_tick:
                pending.extend(snap.events)
                self._last_event_tick = snap.tick
        if pending:
            self._fire_events(pending, snapshots[-1][1])

        # Prediction runs on the newest authoritative word, not the delayed
        # view: it is meant to be ahead of the network, not behind it. Its
        # age matters as much as its contents -- see LocalPredictor._catch_up.
        newest_at, newest = snapshots[-1]
        self._advance_prediction(newest, timing.now() - newest_at, frame_dt)

        # Render INTERP_DELAY in the past, so there is a snapshot on each side
        # of this instant to interpolate between. Drawing the newest snapshot
        # as it lands would move the world in snapshot-rate lurches across a
        # faster screen; this trades a fixed delay for continuous motion.
        target = timing.now() - C.INTERP_DELAY

        pair = select_pair(snapshots, target)
        if pair is None:
            # No bracketing pair: we just joined, or the stream stalled for
            # longer than the buffer holds. Showing the newest snapshot is a
            # visible snap, so it is the fallback, never the normal path.
            view = snapshots[-1][1]
        else:
            older, newer, t = pair
            view = interpolate(older[1], newer[1], t,
                               skip_ids=self._local_ids())

        # Last word on the local dino: everyone else is the host's version,
        # delayed and smoothed; yours is the one you are driving right now.
        self._predictor.apply_to(view)
        self._present(view, frame_dt)

    def _advance_prediction(self, authoritative, age: float,
                            frame_dt: float) -> None:
        """Step the local dino forward, then ease it toward the host's truth."""
        app = App.get_running_app()
        local_id = None
        if app is not None and app.client is not None:
            local_id = app.client.player_id
        if local_id is None:
            return

        auth = next((p for p in authoritative.players if p.id == local_id),
                    None)
        if auth is None:
            self._predictor.stop()
            return

        if not self._predictor.active:
            self._predictor.begin(auth)
            return

        mods = modifiers_for(authoritative.effects)
        # Order matters: advance first, then correct. Correcting a prediction
        # that has not yet accounted for this frame's input would blend away
        # the jump before it was ever drawn.
        self._predictor.step(frame_dt, mods)
        self._predictor.reconcile(auth, age, mods)

    def _local_ids(self) -> frozenset[int]:
        """The player this device controls, who must not be interpolated."""
        app = App.get_running_app()
        if app is None or app.client is None or app.client.player_id is None:
            return frozenset()
        return frozenset({app.client.player_id})

    # -- presentation -------------------------------------------------------

    def _present(self, state, frame_dt: float = 0.0) -> None:
        # Juice is presentation-only, so it is driven here rather than in the
        # sim -- which keeps it out of the network state and out of physics.
        self._consume_events(state)
        self.renderer.advance(frame_dt, state)
        self.renderer.draw(state)

        self.score_stat.set_value(fmt.score(state.score))
        self.progress_label.text = (f"{fmt.distance(state.distance)}   -   "
                                    f"{fmt.speed(state.speed)}")

        self._update_effects(state)

        tension = max((rope_tension(state.players, i)
                       for i in range(max(0, len(state.players) - 1))),
                      default=0.0)
        self.tension_meter.set_value(tension)

        self._update_nameplates(state)
        self._update_net_label()

    def _update_effects(self, state) -> None:
        """One badge per active team power-up, in that power-up's colour.

        Badges are pooled by kind and the row is only rebuilt when the *set*
        of active effects changes -- this runs every frame, and churning
        widgets 60 times a second is how you turn a HUD into a stutter.
        """
        active: list[tuple[str, str]] = []
        if state.shield:
            active.append((C.POWERUP_SHIELD,
                           C.POWERUP_LABELS[C.POWERUP_SHIELD].upper()))
        for kind, remaining in state.effects.items():
            label = C.POWERUP_LABELS.get(kind, kind).upper()
            active.append((kind, f"{label}  {remaining:.0f}s"))

        kinds = [kind for kind, _ in active]
        if kinds != self._effect_kinds:
            self._effect_kinds = kinds
            self.effects_row.clear_widgets()
            # Flexible spacers either side: a plain BoxLayout would pin the
            # badges to the left of the row instead of centring them.
            self.effects_row.add_widget(Widget())
            for kind in kinds:
                badge = self._effect_badges.get(kind)
                if badge is None:
                    badge = Badge("", color=C.POWERUP_COLORS.get(
                        kind, theme.ACCENT), filled=True)
                    self._effect_badges[kind] = badge
                self.effects_row.add_widget(badge)
            self.effects_row.add_widget(Widget())

        for kind, text in active:
            self._effect_badges[kind].text = text

    def _consume_events(self, state) -> None:
        # Clients fire events from arriving snapshots instead (see
        # _frame_client), so only the locally simulated modes go through here.
        if self._mode == "client" or not state.events:
            return
        self._fire_events(state.events, state)

    def fire_reliable_events(self, events: list[dict]) -> None:
        """Events the host sent over TCP because they must not be lost.

        Only crashes take this path. They are stripped from the snapshot
        stream host-side, so there is no risk of firing one twice.
        """
        if not events:
            return
        app = App.get_running_app()
        snapshots = app.snapshots if app is not None else None
        if not snapshots:
            audio.play_events(events)
            return
        self._fire_events(events, snapshots[-1][1])

    def _fire_events(self, events: list[dict], state) -> None:
        audio.play_events(events)
        for event in events:
            if event.get("e") != "crash":
                continue
            crashed = next((p for p in state.players
                            if p.id == event.get("player")), None)
            if crashed is not None:
                box = crashed.hitbox()
                self.renderer.burst(box.x + box.w * 0.5, box.y + box.h * 0.5)
                self.renderer.shake(1.0)

    def _update_nameplates(self, state) -> None:
        for index, plate in enumerate(self.nameplates):
            if index >= len(state.players):
                plate.opacity = 0.0
                continue
            player = state.players[index]
            x, y = self.renderer.player_screen_pos(player)
            plate.text = player.name
            plate.color = C.SKIN_COLORS[player.skin % len(C.SKIN_COLORS)]
            plate.center_x = x
            plate.y = y
            plate.opacity = 1.0

    def _update_net_label(self) -> None:
        app = App.get_running_app()
        if app is None or self._mode == "local":
            self.net_label.text = ""
            return
        if self._mode == "host":
            count = app.host.player_count() if app.host else 1
            self.net_label.text = (f"Hosting - {count} player"
                                   f"{'' if count == 1 else 's'}")
            return
        client = app.client
        if client is None or not client.connected:
            self.net_label.text = "Disconnected"
            return
        if client.rtt is None:
            self.net_label.text = "Connecting..."
        else:
            # Named, not just numeric: "180 ms" means nothing to most players,
            # "poor connection" tells them why the dinos are stuttering.
            ms = client.rtt * 1000.0
            quality = ("Good" if ms < 60 else
                       ("OK" if ms < 150 else "Poor"))
            self.net_label.text = f"{quality} connection - {ms:.0f} ms"

    # -- transitions --------------------------------------------------------

    def _end_local_run(self) -> None:
        assert self.sim is not None
        state = self.sim.state
        self._teardown()
        over = self.manager.get_screen(GAMEOVER)
        over.show_result(
            score=state.score, distance=state.distance,
            cause_player_id=state.cause_player_id,
            cause_obstacle=state.cause_obstacle,
            players=len(state.players),
            seconds=state.tick * C.TICK_DT,
        )
        self.manager.current = GAMEOVER

    # -- leaving a run ------------------------------------------------------

    def request_exit(self) -> None:
        """Pause / confirm before ending a run. Back and the Pause button.

        Back on Android is an edge swipe, and in landscape the edge of the
        screen is where your thumb rests holding the phone. It used to end the
        run outright: one accidental brush and a good run was gone with no
        undo. Now it asks.

        Solo runs are genuinely PAUSED while it asks -- the clock stops.
        Networked runs are not: stopping the host's clock would freeze the
        world for every other player to spare one person a decision, so those
        get the same question with the run still going. That is the honest
        trade, and it is why the wording differs.
        """
        if self._exit_dialog is not None:
            return  # already asking

        solo = self._mode == "local"
        if solo:
            self._pause()

        dialog = ConfirmDialog(
            title="Paused" if solo else "Leave the run?",
            message=("Your run is on hold. Nothing is lost." if solo else
                     "The others keep running without you, and the run "
                     "carries on while you decide."),
            confirm_text="End run",
            cancel_text="Resume" if solo else "Keep playing",
            on_confirm=self._quit,
            on_cancel=self._resume if solo else self._clear_exit_dialog,
        )
        self._exit_dialog = dialog
        dialog.open()

    def _pause(self) -> None:
        if self._event is not None:
            self._event.cancel()
            self._event = None

    def _resume(self) -> None:
        self._clear_exit_dialog()
        # A finger held on the screen when the dialog opened never sent its
        # touch_up, so without this the dino resumes mid-duck forever.
        if self.mapper is not None:
            self.mapper.reset()
        if self._event is None and self.mapper is not None:
            self._accumulator = 0.0  # do not fast-forward the paused seconds
            self._event = Clock.schedule_interval(self._frame, 0)

    def _clear_exit_dialog(self) -> None:
        self._exit_dialog = None

    def _quit(self) -> None:
        self._clear_exit_dialog()
        self._teardown()
        app = App.get_running_app()
        if app is not None:
            app.leave_network()
        self.manager.current = MENU
