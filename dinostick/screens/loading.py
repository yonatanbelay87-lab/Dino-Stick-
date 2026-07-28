"""Boot screen: the wordmark drops in, a dino runs the rope, then the menu.

Shown once, on launch, before anything else. It exists for two reasons and the
second one is the real one:

  * A cold start on Android is genuinely slow. Python and Kivy take seconds to
    boot before a single line of this file runs, and the presplash covers that
    -- but the work THIS module does (building the sky and sun textures,
    decoding the first season backdrop, loading five sound files) would
    otherwise happen on the first frame of the menu or, worse, on the first
    frame of a run. Doing it here turns an invisible stutter into a deliberate
    two seconds.

  * It is the first thing anybody sees, so it is where the game gets to say
    what it is.

The progress bar is not a lie. It tracks real tasks (see TASKS) and cannot
reach 100% until they have all finished -- but it also cannot reach 100% before
MIN_SECONDS, because work that completes in 40ms would otherwise flash past
unread. Both conditions have to hold, which is one ``min()``:

    target = min(work_done_fraction, elapsed / MIN_SECONDS)

...and a hard ceiling at MAX_SECONDS so a wedged asset load can never strand
the player on a splash screen with no way forward.
"""

from __future__ import annotations

import math
import os

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, RoundedRectangle
from kivy.loader import Loader
from kivy.metrics import dp
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.widget import Widget

from game import constants as C
from ui import audio, theme
from ui import scene as scene_mod
from ui.insets import insets
from ui.scene import SunsetScene
from ui.widgets import Caption, DinoAvatar, GameTitle, Pop, animate

from . import MENU

# The bar fills over at least this long, so the animation is actually seen...
MIN_SECONDS = 2.0
# ...and no longer than this, whatever the asset loads are doing. A splash with
# no exit is the worst dead end in an app.
MAX_SECONDS = 6.0

# How long "READY!" and the dino's flip get before the menu slides in.
CELEBRATE_SECONDS = 0.55

# Longest single frame this screen will believe. The first dt after app start
# is a catch-up value covering everything Kivy did before the Clock started
# ticking -- measured at ~1.2s on desktop, and a cold Android boot is worse.
# Taken at face value it burned more than half the animation before the first
# frame was drawn. Same reasoning as C.MAX_FRAME_DT in the game loop.
MAX_TICK_DT = 0.05

# Dino gait.
BOB_HZ = 3.4          # strides per second
BOB_HEIGHT = dp(7)    # how far the body rises mid-stride
DUST_EVERY = 0.07     # seconds between puffs
DUST_LIFETIME = 0.45
DUST_RADIUS = dp(7)


def _load_backdrops() -> list:
    """Start decoding the first two season images, via Kivy's shared Loader.

    ``Loader.image`` caches by path and the renderer's own Backdrop asks for the
    same paths, so warming them here is not duplicated work -- the run simply
    finds them already decoded instead of flashing bare hills on frame one.
    """
    directory = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", C.BG_DIR_NAME)
    proxies = []
    for filename, _is_transition in C.BG_SEQUENCE[:2]:
        path = os.path.join(directory, filename)
        if not os.path.exists(path):
            continue
        try:
            proxies.append(Loader.image(path))
        except Exception:
            pass  # a missing backdrop is the renderer's problem, not ours
    return proxies


class RopeLoader(Widget):
    """The progress bar, drawn as the rope the whole game is about.

    A generic bar would have been fine and forgettable. The rope is the one
    mechanic every player has to understand before they can play, so the boot
    screen introduces it: slack dark cord, pulled taut and green as the team
    gets ready, with a knot at each end.
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._progress = 0.0
        self._dust: list[list[float]] = []
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def set_progress(self, value: float) -> None:
        value = max(0.0, min(1.0, value))
        if abs(value - self._progress) < 0.0005:
            return
        self._progress = value
        self._redraw()

    @property
    def fill_x(self) -> float:
        """Screen x of the leading edge -- where the dino's feet go."""
        return self.x + self.width * self._progress

    # -- dust ---------------------------------------------------------------

    def puff(self, x: float) -> None:
        self._dust.append([x, self.top, 0.0])

    def advance_dust(self, dt: float) -> None:
        for puff in self._dust:
            puff[2] += dt
            puff[0] -= dt * dp(30)  # drifts backwards as the dino runs on
            puff[1] += dt * dp(9)   # and lifts, the way dust does
        self._dust = [p for p in self._dust if p[2] < DUST_LIFETIME]
        self._redraw()

    # -- drawing ------------------------------------------------------------

    def _redraw(self, *_args) -> None:
        self.canvas.clear()
        if self.width <= 0 or self.height <= 0:
            return
        radius = self.height * 0.5
        with self.canvas:
            Color(*theme.INK[:3], 0.78)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])

            if self._progress > 0.0:
                Color(*theme.DINO)
                # Never narrower than the cap, or the rounded fill inverts into
                # a sliver at the very start.
                width = max(self.width * self._progress, radius * 2.0)
                RoundedRectangle(pos=self.pos, size=(width, self.height),
                                 radius=[radius])

            # A knot at each end: this is a rope with two dinos on it, not a
            # meter that happens to be round.
            knot = radius * 1.55
            Color(*theme.TAN)
            for cx in (self.x, self.right):
                Ellipse(pos=(cx - knot, self.center_y - knot),
                        size=(knot * 2, knot * 2))

            # Dust last, so it reads as kicked up OVER the rope. Drawn first it
            # sat behind a translucent track and just muddied it.
            for x, y, age in self._dust:
                fade = 1.0 - age / DUST_LIFETIME
                size = DUST_RADIUS * (0.45 + 1.1 * (1.0 - fade))
                Color(*theme.CREAM[:3], 0.34 * fade)
                Ellipse(pos=(x - size, y - size), size=(size * 2, size * 2))


class LoadingScreen(Screen):
    """One Screen in the ScreenManager, like every other."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)

        # (label shown while it runs, the work). Each runs on its own frame so
        # the bar and the dino keep moving through them.
        self.tasks = (
            ("Painting the sunset", scene_mod.sky_texture),
            ("Lighting the sun", scene_mod.sun_texture),
            ("Tuning the rope", audio.preload),
            ("Rolling out the canyon", self._begin_backdrops),
        )

        self._event = None
        self._elapsed = 0.0
        self._step = 0
        self._progress = 0.0
        self._dust_timer = 0.0
        self._proxies: list = []
        self._finishing = False

        root = FloatLayout()
        self.scene = SunsetScene(size_hint=(1, 1))
        root.add_widget(self.scene)

        # -- wordmark, in a transform so it can bounce without re-rendering --
        self.title_pop = Pop(size_hint=(None, None))
        # A FloatLayout leaves a child with no pos_hint wherever it happens to
        # be, which is (0, 0) -- the corner of the window. The dino uses
        # pos_hint; the title is driven from _layout instead, because a
        # size_hint_y of None plus a pos_hint did not take here and an explicit
        # copy is one line and never ambiguous.
        self.title = GameTitle()
        self.title_pop.add_widget(self.title)
        root.add_widget(self.title_pop)

        self.tagline = Caption("Co-op endless runner", theme.FONT_SMALL,
                               color=theme.FAINT, size_hint=(None, None))
        root.add_widget(self.tagline)

        # -- the rope --------------------------------------------------------
        self.rope = RopeLoader(size_hint=(None, None), size=(dp(320), dp(14)))
        root.add_widget(self.rope)

        # The runner. Unframed -- it is on the rope, not in a roster portrait
        # -- and inside a Pop so it can flip at the end.
        self.dino_pop = Pop(size_hint=(None, None), size=(dp(42), dp(42)))
        self.dino = DinoAvatar(0, framed=False, size_hint=(1, 1),
                               pos_hint={"x": 0, "y": 0})
        self.dino_pop.add_widget(self.dino)
        root.add_widget(self.dino_pop)

        self.status = Caption("", theme.FONT_SMALL, color=theme.FAINT,
                              size_hint=(None, None))
        root.add_widget(self.status)

        # halign/valign matter here because text_size is set in _layout: without
        # them the default left alignment pinned "READY!" to the screen edge.
        self.ready = Label(text="READY!", font_size=theme.FONT_TITLE,
                           font_name=theme.FONT_DISPLAY_NAME,
                           color=theme.DINO, opacity=0.0,
                           halign="center", valign="middle",
                           size_hint=(None, None))
        root.add_widget(self.ready)

        self.root_layout = root
        self.add_widget(root)

        root.bind(pos=self._layout, size=self._layout)
        insets.bind_layout(self._layout)
        self._layout()

    # -- layout -------------------------------------------------------------

    def _layout(self, *_args) -> None:
        root = self.root_layout
        self.scene.pos, self.scene.size = root.pos, root.size

        sx, sy, sw, sh = insets.box(root.width, root.height)
        if sw <= 0 or sh <= 0:
            return
        sx, sy = root.x + sx, root.y + sy
        centre_x = sx + sw * 0.5

        self.title.width = sw
        self.title_pop.size = (sw, self.title.height)
        self.title_pop.center_x = centre_x
        self.title_pop.center_y = sy + sh * 0.66
        self.title.size = self.title_pop.size
        self.title.pos = self.title_pop.pos

        self.tagline.width = sw
        self.tagline.center_x = centre_x
        self.tagline.top = self.title_pop.y - theme.SPACE_1

        # The rope is the widest thing on screen but never edge to edge: a bar
        # that touches both walls has no room for the end knots to read.
        self.rope.width = min(sw - 2 * theme.SPACE_6, dp(520))
        self.rope.center_x = centre_x
        self.rope.center_y = sy + sh * 0.36

        self.status.width = sw
        self.status.center_x = centre_x
        self.status.top = self.rope.y - theme.SPACE_5

        self.ready.size = (sw, theme.FONT_TITLE * 1.5)
        self.ready.text_size = self.ready.size
        self.ready.center_x = centre_x
        self.ready.center_y = self.status.center_y

        self._place_dino()

    def _place_dino(self) -> None:
        """Feet on the rope, at the leading edge of the fill."""
        if self._finishing:
            return  # the flip animation owns the position from here
        bob = abs(math.sin(self._elapsed * BOB_HZ * math.pi)) * BOB_HEIGHT
        self.dino_pop.center_x = self.rope.fill_x
        self.dino_pop.y = self.rope.center_y + self.rope.height * 0.3 + bob

    # -- lifecycle ----------------------------------------------------------

    def on_enter(self, *_args) -> None:
        # ScreenManager can dispatch on_enter more than once for the very first
        # screen -- once directly and once when the transition completes.
        # Without this, the second call scheduled a SECOND tick loop, orphaned
        # the first (self._event only holds one reference, so it could never be
        # cancelled), and the two of them advanced _elapsed twice per frame:
        # the whole boot ran at double speed and the leaked loop never stopped.
        self._stop()

        app_skin = 0
        try:
            from kivy.app import App  # noqa: PLC0415

            app = App.get_running_app()
            if app is not None:
                app_skin = int(getattr(app, "skin", 0))
        except Exception:
            pass
        self.dino.set_skin(app_skin)

        self._elapsed = 0.0
        self._step = 0
        self._progress = 0.0
        self._finishing = False
        self.rope.set_progress(0.0)
        self.ready.opacity = 0.0
        self.dino_pop.spin = 0.0
        self.dino_pop.scale = 1.0
        self.status.text = self.tasks[0][0] + "..."

        # The wordmark drops in slightly oversized and settles -- out_back
        # overshoots, which is what makes it read as a bounce rather than a
        # fade. Everything else is already on screen and simply waiting.
        self.title_pop.scale = 0.62
        self.title_pop.opacity = 0.0
        animate(self.title_pop, theme.T_FADE, "out_quad", opacity=1.0)
        animate(self.title_pop, 0.42, theme.EASE_SPRING, scale=1.0)

        self._event = Clock.schedule_interval(self._tick, 0)

    def on_leave(self, *_args) -> None:
        self._stop()

    def _stop(self) -> None:
        if self._event is not None:
            self._event.cancel()
            self._event = None

    # -- the loop -----------------------------------------------------------

    def _begin_backdrops(self) -> None:
        self._proxies = _load_backdrops()

    def _backdrops_ready(self) -> bool:
        return all(getattr(p, "loaded", False) for p in self._proxies)

    def _work_fraction(self) -> float:
        """How much of the real work is done, 0..1.

        The backdrop decode is asynchronous, so it counts as one extra unit
        beyond the task list -- otherwise the bar would hit 100% while the
        image was still being decoded on a Loader thread.
        """
        total = len(self.tasks) + 1
        done = self._step
        if self._step >= len(self.tasks) and self._backdrops_ready():
            done += 1
        return min(1.0, done / total)

    def _tick(self, dt: float) -> None:
        dt = min(dt, MAX_TICK_DT)
        self._elapsed += dt

        # One task per frame, so the bar and the dino keep moving through them
        # instead of the whole screen freezing on the slow one.
        if self._step < len(self.tasks):
            label, work = self.tasks[self._step]
            try:
                work()
            except Exception:
                pass  # a failed preload is a slower first frame, not a crash
            self._step += 1

        target = min(self._work_fraction(), self._elapsed / MIN_SECONDS)
        if self._elapsed >= MAX_SECONDS:
            target = 1.0  # never strand the player on a splash

        # Ease toward the target rather than snapping: work finishing in one
        # frame would otherwise jump the bar and teleport the dino.
        self._progress += (target - self._progress) * min(1.0, dt * 7.0)
        if target >= 1.0 and self._progress > 0.995:
            self._progress = 1.0
        self.rope.set_progress(self._progress)

        # The caption follows the BAR, not the task index. On a desktop all four
        # tasks finish inside the first few frames, so indexing by task pinned
        # the label to the last one for 90% of the fill; on a slow phone the
        # work gates the bar anyway, so the two stay in step where it matters.
        phase = min(len(self.tasks) - 1,
                    int(self._progress * len(self.tasks)))
        self.status.text = f"{self.tasks[phase][0]}..."

        self._dust_timer += dt
        if self._progress < 1.0 and self._dust_timer >= DUST_EVERY:
            self._dust_timer = 0.0
            self.rope.puff(self.rope.fill_x - dp(6))
        self.rope.advance_dust(dt)
        self._place_dino()

        if self._progress >= 1.0:
            self._finish()

    # -- the finish ---------------------------------------------------------

    def _finish(self) -> None:
        if self._finishing:
            return
        self._finishing = True
        self._stop()

        self.status.text = ""
        self.ready.opacity = 1.0
        animate(self.ready, theme.T_ENTER, theme.EASE_SPRING, opacity=1.0)

        # A 360 hop: up, over, down. The spin and the arc run together, which
        # is why the tick loop stops owning the dino's position first.
        self.dino_pop.center_x = self.rope.right
        base_y = self.rope.center_y + self.rope.height * 0.3
        if theme.REDUCE_MOTION:
            self.dino_pop.y = base_y
        else:
            hop = (Animation(y=base_y + dp(46), d=0.24, t="out_quad")
                   + Animation(y=base_y, d=0.24, t="in_quad"))
            hop.start(self.dino_pop)
            Animation(spin=-360.0, d=0.48, t="in_out_cubic").start(self.dino_pop)

        Clock.schedule_once(lambda _dt: self._go_to_menu(), CELEBRATE_SECONDS)

    def _go_to_menu(self) -> None:
        if self.manager is not None and self.manager.current == self.name:
            self.manager.current = MENU
