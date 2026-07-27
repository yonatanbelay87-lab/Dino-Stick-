"""Input mapping: keyboard (desktop) and touch zones (Android) -> jump/duck.

Produces plain ``InputState`` values the simulation consumes, so the sim never
knows whether a jump came from a key, a tap, or the network.

Supports several local players at once from one keyboard, which is what Phase 2
needs for same-screen co-op. Touch zones arrive in Phase 5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kivy.core.window import Keyboard

from . import constants as C

# Kivy hands key events to us as integer keycodes; the bindings table in
# constants.py is written with human-readable names, so invert Kivy's map once.
_CODE_TO_NAME: dict[int, str] = {
    code: name for name, code in Keyboard.keycodes.items()
}


@dataclass
class InputState:
    jump: bool = False
    duck: bool = False


class InputMapper:
    """Tracks held keys and exposes the current InputState per local player."""

    def __init__(self, num_players: int = 1) -> None:
        self.states: list[InputState] = [InputState() for _ in range(num_players)]

        # key name -> (player index, action)
        self._keys: dict[str, tuple[int, str]] = {}
        for index in range(min(num_players, len(C.KEYBOARD_BINDINGS))):
            for action, names in C.KEYBOARD_BINDINGS[index].items():
                for name in names:
                    self._keys[name] = (index, action)

        self._bound_window: Any = None

        # Touch state for local player 0: finger id -> action.
        self._touches: dict[Any, str] = {}
        # Keys currently held for player 0, kept separately so touch and
        # keyboard can be OR-ed instead of overwriting each other.
        self._keys_down: dict[str, bool] = {}

    # -- binding ------------------------------------------------------------

    def bind_keyboard(self, window: Any) -> None:
        if self._bound_window is not None:
            return
        window.bind(on_key_down=self._on_key_down, on_key_up=self._on_key_up)
        self._bound_window = window

    def unbind_keyboard(self) -> None:
        if self._bound_window is None:
            return
        self._bound_window.unbind(
            on_key_down=self._on_key_down, on_key_up=self._on_key_up
        )
        self._bound_window = None

    # -- handlers -----------------------------------------------------------
    # Kivy's on_key_down and on_key_up carry different argument counts, so both
    # take *args and read only the keycode.

    def _on_key_down(self, _window: Any, keycode: int, *_args: Any) -> bool:
        return self._set(keycode, True)

    def _on_key_up(self, _window: Any, keycode: int, *_args: Any) -> bool:
        return self._set(keycode, False)

    def _set(self, keycode: int, value: bool) -> bool:
        name = _CODE_TO_NAME.get(keycode)
        if name is None:
            return False
        binding = self._keys.get(name)
        if binding is None:
            return False
        index, action = binding
        if index == 0:
            # Player 0 may also be driven by touch; merge rather than clobber.
            self._keys_down[action] = value
            self._apply_touches()
        else:
            setattr(self.states[index], action, value)
        return True  # consume, so the key doesn't also scroll/exit

    # -- touch ---------------------------------------------------------------
    #
    # Touch always drives local player 0 (on a phone you control one dino; the
    # others are on their own devices). Every finger currently down is tracked
    # by id, so holding duck with one thumb and tapping jump with the other
    # works, and lifting one finger cannot clear the other's action.

    def zone_for(self, x: float, y: float, width: float, height: float
                 ) -> str | None:
        """Which action a point falls in, or None for dead zones."""
        if width <= 0 or height <= 0:
            return None
        fx, fy = x / width, y / height
        if fy > 1.0 - C.TOUCH_DEAD_ZONE_TOP:
            return None  # reserved for HUD buttons
        for action, (zx, zy, zw, zh) in C.TOUCH_ZONES.items():
            if zx <= fx < zx + zw and zy <= fy < zy + zh:
                return action
        return None

    def touch_down(self, touch_id: Any, x: float, y: float,
                   width: float, height: float) -> bool:
        action = self.zone_for(x, y, width, height)
        if action is None:
            return False
        self._touches[touch_id] = action
        self._apply_touches()
        return True

    def touch_move(self, touch_id: Any, x: float, y: float,
                   width: float, height: float) -> bool:
        if touch_id not in self._touches:
            return False
        action = self.zone_for(x, y, width, height)
        if action is None:
            del self._touches[touch_id]
        else:
            self._touches[touch_id] = action
        self._apply_touches()
        return True

    def touch_up(self, touch_id: Any) -> bool:
        if self._touches.pop(touch_id, None) is None:
            return False
        self._apply_touches()
        return True

    def _apply_touches(self) -> None:
        if not self.states:
            return
        active = set(self._touches.values())
        # Touch and keyboard are OR-ed, so plugging a keyboard into a tablet
        # does not fight with the on-screen zones.
        self.states[0].jump = self._keys_down.get("jump", False) or "jump" in active
        self.states[0].duck = self._keys_down.get("duck", False) or "duck" in active

    def reset(self) -> None:
        self._touches.clear()
        self._keys_down.clear()
        for state in self.states:
            state.jump = False
            state.duck = False
