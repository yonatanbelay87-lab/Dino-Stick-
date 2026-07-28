"""Safe-area insets -- the strips of screen the system has already claimed.

A modern Android phone is not a rectangle you get all of. Somewhere along one
edge there is a camera cutout; there is a status bar; and at the bottom there
is either a three-button navigation bar or the gesture pill. This game is
locked to LANDSCAPE, which is the worst case for all three: the cutout and the
nav bar both move to the *sides*, which is exactly where a HUD wants to live.

Nothing in the app read any of this before. On a notched phone the Quit button
sat under the camera and the control hint ran under the gesture bar.

Two halves:

  * ``enable_edge_to_edge()`` asks Android to let us draw into the cutout area
    instead of letterboxing us away from it. The painted season backdrops want
    the whole screen; only the *controls* need to stay clear.
  * ``insets`` reports how much of each edge is unsafe, in Kivy window pixels,
    as bindable properties so a widget can re-lay-itself-out when the gesture
    bar appears or the device rotates.

Everything here is best-effort. A wrong inset is a cosmetic bug; a crash on
startup is not. Every path degrades to "no insets" rather than raising, and
edge-to-edge is only switched on once we have proved we can read the insets
back -- otherwise we would be drawing under a notch we cannot measure.
"""

from __future__ import annotations

import os

from kivy.clock import Clock
from kivy.event import EventDispatcher
from kivy.metrics import dp
from kivy.properties import NumericProperty

ANDROID = "ANDROID_ARGUMENT" in os.environ

# The gesture bar hides and reappears, a rotation changes every edge at once,
# and neither fires a Kivy event. One JNI call a second is nothing next to a
# 60fps render loop.
POLL_SECONDS = 1.0

# Used only when edge-to-edge is ON but a later query fails: we are definitely
# drawing under something, we just cannot measure it this instant. Roughly a
# gesture bar's worth, so controls stay reachable instead of vanishing.
FALLBACK = dp(20)

# android.view.WindowManager$LayoutParams
_CUTOUT_SHORT_EDGES = 1


def _activity():
    from jnius import autoclass  # noqa: PLC0415 -- Android only

    return autoclass("org.kivy.android.PythonActivity").mActivity


def _sdk_int() -> int:
    from jnius import autoclass  # noqa: PLC0415 -- Android only

    return autoclass("android.os.Build$VERSION").SDK_INT


def _query() -> tuple[float, float, float, float] | None:
    """Read the current insets in Kivy window pixels, or None if unavailable.

    Two APIs, because the good one is new. From API 30 a single call returns
    the union of the system bars and the cutout. Before that they are separate
    questions, and the cutout only exists at all from API 28 -- which is fine,
    because a phone older than that does not have one.
    """
    if not ANDROID:
        return None
    try:
        from jnius import autoclass  # noqa: PLC0415 -- Android only

        activity = _activity()
        if activity is None:
            return None
        decor = activity.getWindow().getDecorView()
        raw = decor.getRootWindowInsets()  # API 23+
        if raw is None:
            return None

        sdk = _sdk_int()
        if sdk >= 30:
            types = autoclass("android.view.WindowInsets$Type")
            box = raw.getInsets(types.systemBars() | types.displayCutout())
            left, top = float(box.left), float(box.top)
            right, bottom = float(box.right), float(box.bottom)
        else:
            left = float(raw.getSystemWindowInsetLeft())
            top = float(raw.getSystemWindowInsetTop())
            right = float(raw.getSystemWindowInsetRight())
            bottom = float(raw.getSystemWindowInsetBottom())
            if sdk >= 28:
                cutout = raw.getDisplayCutout()
                if cutout is not None:
                    left = max(left, float(cutout.getSafeInsetLeft()))
                    top = max(top, float(cutout.getSafeInsetTop()))
                    right = max(right, float(cutout.getSafeInsetRight()))
                    bottom = max(bottom, float(cutout.getSafeInsetBottom()))
    except Exception:
        return None

    return _to_window_pixels(left, top, right, bottom, decor)


def _to_window_pixels(left, top, right, bottom, decor):
    """Insets come back in decor-view pixels; Kivy measures its own surface.

    Normally the two are identical on Android. They are not on a device where
    SDL took a smaller surface than the decor view (split screen, a desktop
    window mode), and an unscaled inset there would push the HUD into the
    middle of the screen.
    """
    try:
        from kivy.core.window import Window  # noqa: PLC0415 -- lazy: creates it

        decor_w = float(decor.getWidth())
        decor_h = float(decor.getHeight())
        if decor_w > 0 and decor_h > 0:
            sx = Window.width / decor_w
            sy = Window.height / decor_h
            left, right = left * sx, right * sx
            top, bottom = top * sy, bottom * sy
    except Exception:
        pass
    return (max(0.0, left), max(0.0, top), max(0.0, right), max(0.0, bottom))


def enable_edge_to_edge() -> bool:
    """Ask Android to lay us out THROUGH the cutout rather than around it.

    Without this, a landscape app on a notched phone is letterboxed: the system
    leaves a black bar down the notched side and the game loses that strip of
    screen. With it, the backdrop art runs genuinely edge to edge and it
    becomes our job to keep controls out of the notch -- which is what the
    inset values above are for.

    Returns whether it was applied, so the caller can decide not to trust
    edge-to-edge if the flag did not take.
    """
    if not ANDROID or _sdk_int() < 28:
        return False
    try:
        activity = _activity()
        if activity is None:
            return False
        window = activity.getWindow()

        def apply() -> None:
            params = window.getAttributes()
            params.layoutInDisplayCutoutMode = _CUTOUT_SHORT_EDGES
            window.setAttributes(params)

        # Window attributes are UI-thread state. p4a ships a decorator for
        # exactly this; if it is missing, try directly -- on most builds the
        # change is posted rather than rejected, and a failure here just means
        # we stay letterboxed, which is safe.
        try:
            from android.runnable import run_on_ui_thread  # noqa: PLC0415

            run_on_ui_thread(apply)()
        except ImportError:
            apply()
        return True
    except Exception:
        return False


class _Insets(EventDispatcher):
    """Unsafe margin per edge, in Kivy window pixels.

    Kivy properties rather than plain floats so a widget can ``bind()`` and
    re-lay-itself-out when the gesture bar slides away mid-run.
    """

    left = NumericProperty(0.0)
    top = NumericProperty(0.0)
    right = NumericProperty(0.0)
    bottom = NumericProperty(0.0)

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._edge_to_edge = False
        self._started = False

    def start(self) -> None:
        """Read once, then keep watching. Safe to call more than once."""
        if self._started or not ANDROID:
            return
        self._started = True

        # Order matters: prove we can measure the insets BEFORE asking to draw
        # under them. If the query does not work on this device we stay
        # letterboxed, which costs a black bar and guarantees nothing clips.
        first = _query()
        if first is not None:
            self._edge_to_edge = enable_edge_to_edge()
        self._apply(first)

        from kivy.core.window import Window  # noqa: PLC0415

        Window.bind(on_resize=lambda *_: self.refresh())
        Clock.schedule_interval(lambda _dt: self.refresh(), POLL_SECONDS)
        # The cutout flag is applied on the UI thread a beat later than this
        # runs, so the first useful measurement arrives on the next frame.
        Clock.schedule_once(lambda _dt: self.refresh(), 0)

    def refresh(self, *_args) -> None:
        self._apply(_query())

    def _apply(self, values) -> None:
        if values is None:
            if not self._edge_to_edge:
                return  # letterboxed by the system: zero really is correct
            values = (FALLBACK, 0.0, FALLBACK, FALLBACK)
        self.left, self.top, self.right, self.bottom = values

    # -- helpers screens use ------------------------------------------------

    @property
    def padding(self) -> list[float]:
        """Kivy padding order: left, top, right, bottom."""
        return [self.left, self.top, self.right, self.bottom]

    def box(self, width: float, height: float
            ) -> tuple[float, float, float, float]:
        """The safe rectangle inside a widget of this size: (x, y, w, h)."""
        return (self.left,
                self.bottom,
                max(0.0, width - self.left - self.right),
                max(0.0, height - self.top - self.bottom))

    def bind_layout(self, callback) -> None:
        """Run ``callback`` whenever any edge changes."""
        self.bind(left=callback, top=callback, right=callback, bottom=callback)


insets = _Insets()
