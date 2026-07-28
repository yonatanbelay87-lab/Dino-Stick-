"""Draws a GameState snapshot onto a Kivy widget's canvas.

Read-only with respect to the simulation: it never mutates state, so the same
renderer serves the host (live state) and clients (interpolated snapshots).

Coordinates: the sim works in a fixed DESIGN_WIDTH x DESIGN_HEIGHT space with
y measured up from the ground line. The renderer fits that space to the real
widget with a single uniform scale (see _scale), so devices with different
screen shapes agree on where everything is in world terms AND draw the same
undistorted characters -- a phone panel is nowhere near 16:9, and scaling the
axes independently to fill it squashed every dino.

The renderer owns a little transient visual state of its own -- screen shake
and crash particles -- because those are pure presentation and should never
travel over the network or affect the simulation.
"""

from __future__ import annotations

import math
import random
from typing import Any

from kivy.graphics import (Color, Ellipse, Line, PopMatrix, PushMatrix,
                           Rectangle, Translate, Triangle)

from . import constants as C
from .backdrop import Backdrop
from .entities import GameState, Player
from .physics import rope_tension


def _shade(color, factor: float):
    """Lighten/darken a skin colour for a body part, keeping alpha."""
    if factor == 1.0:
        return color
    return (min(1.0, color[0] * factor), min(1.0, color[1] * factor),
            min(1.0, color[2] * factor), color[3])


class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "color")

    def __init__(self, x: float, y: float, color) -> None:
        angle = random.uniform(0.0, math.tau)
        speed = random.uniform(0.35, 1.0) * C.PARTICLE_SPEED
        self.x = x
        self.y = y
        self.vx = math.cos(angle) * speed
        self.vy = math.sin(angle) * speed + C.PARTICLE_SPEED * 0.4
        self.life = C.PARTICLE_LIFETIME
        self.color = color


class Renderer:
    """Owns the canvas instructions for one game view."""

    def __init__(self, widget: Any) -> None:
        self.widget = widget
        self._particles: list[Particle] = []
        self._shake = 0.0
        self._prev_tension = 0.0
        self._scroll = 0.0  # parallax offset, advanced from world speed
        self.backdrop = Backdrop()
        # Metres run, smoothed -- see advance(). Drives the season strip.
        self._bg_metres = 0.0
        # Decode the opening season now rather than on the first frame of the
        # first run: this object is built while the menu is still up.
        self.backdrop.warm(0.0)

    def reset(self) -> None:
        """Drop everything carried over from the previous run."""
        self._particles.clear()
        self._shake = 0.0
        self._prev_tension = 0.0
        self._scroll = 0.0
        self._bg_metres = 0.0
        self.backdrop.warm(0.0)

    # -- juice hooks --------------------------------------------------------

    def burst(self, x: float, y: float, color=C.COLOR_DANGER) -> None:
        """Spray particles at a world position (used on crash)."""
        for _ in range(C.PARTICLE_COUNT):
            self._particles.append(Particle(x, y, color))

    def shake(self, amount: float = 1.0) -> None:
        self._shake = max(self._shake, C.SHAKE_DURATION * amount)

    def advance(self, dt: float, state: GameState) -> None:
        """Step the presentation-only animation. Call once per rendered frame."""
        self._scroll += state.speed * dt
        self._advance_backdrop(dt, state)

        if self._shake > 0.0:
            self._shake = max(0.0, self._shake - dt)

        alive = []
        for p in self._particles:
            p.life -= dt
            if p.life <= 0.0:
                continue
            p.vy += C.PARTICLE_GRAVITY * dt
            p.x += p.vx * dt
            p.y += p.vy * dt
            alive.append(p)
        self._particles = alive

        # A rope that snaps from slack to fully taut in one frame is a hard
        # yank -- worth a jolt. Rising edge only, so a sustained taut rope
        # does not shake forever.
        tension = max((rope_tension(state.players, i)
                       for i in range(max(0, len(state.players) - 1))),
                      default=0.0)
        if (tension >= C.SHAKE_TENSION_TRIGGER
                and self._prev_tension < C.SHAKE_TENSION_TRIGGER):
            self.shake(0.6)
        self._prev_tension = tension

    def _advance_backdrop(self, dt: float, state: GameState) -> None:
        """Track ``state.distance`` in metres, smoothly.

        The season strip could read state.distance directly, and in local and
        host mode that would be the end of it. A client cannot: its distance
        arrives in snapshot-rate steps, and scenery that only moves ~20 times a
        second next to obstacles interpolated every frame reads as a judder in
        the background of an otherwise smooth game.

        So the count is advanced locally at the world speed -- which every mode
        already has, every frame -- and continuously eased back onto the
        authoritative figure. A gap too big to be lag (a new run, a rematch, a
        client dropping into a run already in progress) is taken in one jump
        instead, because easing across half a kilometre would scroll the whole
        cycle past at a ludicrous speed.
        """
        target = state.distance / C.PIXELS_PER_METRE
        if abs(target - self._bg_metres) > C.BG_RESYNC_METRES:
            self._bg_metres = target
            return
        self._bg_metres += state.speed * dt / C.PIXELS_PER_METRE
        self._bg_metres += ((target - self._bg_metres)
                            * min(1.0, C.BG_CORRECT_RATE * dt))

    # -- design space -> widget space ---------------------------------------

    def _scale(self) -> float:
        """One scale for both axes, so nothing is ever stretched or squashed.

        Sized to the width (see VIEW_MIN_HEIGHT), which is what keeps the whole
        runway visible on any screen shape; the cap stops a squat window from
        scaling the world up until the jump apex leaves the screen.
        """
        return min(self.widget.width / C.DESIGN_WIDTH,
                   self.widget.height / C.VIEW_MIN_HEIGHT)

    def _origin(self) -> tuple[float, float]:
        """Where design (0, 0) lands in widget coordinates.

        Ground-anchored: spare height is sky above, never a gap below the
        ground line. Spare width -- only possible on a very squat window --
        goes on the LEFT, so the design's right edge stays flush with the
        screen's. Obstacles spawn just off that edge, and pushing it inward
        would let players watch them appear out of nothing.
        """
        s = self._scale()
        return (self.widget.right - C.DESIGN_WIDTH * s, self.widget.y)

    def _visible_x(self) -> tuple[float, float]:
        """The design-space x range the widget actually shows."""
        s = self._scale()
        ox, _ = self._origin()
        return ((self.widget.x - ox) / s, (self.widget.right - ox) / s)

    def _place(self, x: float, y: float, w: float, h: float
               ) -> tuple[tuple[float, float], tuple[float, float]]:
        """Map a sim-space rect (y = height above ground) to widget pos/size."""
        s = self._scale()
        ox, oy = self._origin()
        return ((ox + x * s, oy + (C.GROUND_Y + y) * s), (w * s, h * s))

    # -- drawing ------------------------------------------------------------

    def draw(self, state: GameState) -> None:
        widget = self.widget
        widget.canvas.clear()

        offset = (0.0, 0.0)
        if self._shake > 0.0:
            mag = C.SHAKE_MAGNITUDE * (self._shake / C.SHAKE_DURATION)
            offset = (random.uniform(-mag, mag), random.uniform(-mag, mag))

        with widget.canvas:
            Color(*C.COLOR_BG)
            Rectangle(pos=widget.pos, size=widget.size)

            PushMatrix()
            Translate(offset[0], offset[1], 0)

            # The painted seasons are the background; the old hills stay on as
            # the fallback for the frames before an image has decoded (and for
            # a build with the art missing entirely).
            if not self._draw_backdrop():
                self._draw_parallax()
            self._draw_ground()

            for powerup in state.powerups:
                self._draw_powerup(powerup)

            Color(*C.COLOR_FG)
            for obstacle in state.obstacles:
                w, h = C.OBSTACLE_SIZES[obstacle.kind]
                pos, size = self._place(obstacle.x, obstacle.y, w, h)
                Rectangle(pos=pos, size=size)

            # Rope first, so the dinos draw over the knots.
            self._draw_rope(state)
            for player in state.players:
                self._draw_player(player, state)

            self._draw_particles()

            PopMatrix()

    def _draw_backdrop(self) -> bool:
        """Draw the season strip. False if there was nothing ready to draw.

        All the placement lives in backdrop.tiles(); this only maps the result
        into widget space and hands it to the canvas.
        """
        scale = self._scale()
        if scale <= 0.0:
            return False
        left, right = self._visible_x()
        view_top = self.widget.height / scale - C.GROUND_Y
        tiles = self.backdrop.tiles(self._bg_metres, left, right, view_top)
        if not tiles:
            return False

        Color(1, 1, 1, 1)  # untinted: whatever colour ran before is not ours
        for texture, x, y, w, h in tiles:
            pos, size = self._place(x, y, w, h)
            # A hair wider than exact. Neighbouring copies land on fractional
            # pixels, and without the overlap the rounding shows up as a
            # flickering one-pixel gap of sky between them.
            Rectangle(pos=pos, size=(size[0] + 1.0, size[1]),
                      texture=texture)
        return True

    def _draw_parallax(self) -> None:
        """Rolling hills at fractional scroll speeds, for depth.

        Each hill is the TOP HALF of an ellipse straddling the ground line, so
        it reads as a dome resting on the horizon. Drawing whole ellipses
        instead leaves their lower half hanging below the ground line, since
        nothing occludes it.
        """
        spacing = C.PARALLAX_HILL_SPACING
        width = C.PARALLAX_HILL_WIDTH
        # Tile across whatever is actually on screen, not across the design
        # width: a screen wider than the design space would otherwise end in
        # bare horizon on the left where the hills ran out.
        left, right = self._visible_x()
        for factor, height, color in C.PARALLAX_LAYERS:
            Color(*color)
            shift = (self._scroll * factor) % spacing
            x = left - shift - width
            while x < right + width:
                # Centre the ellipse on the ground line and keep only the top.
                pos, size = self._place(x, -height, width, height * 2.0)
                Ellipse(pos=pos, size=size, angle_start=-90, angle_end=90)
                x += spacing

    def _draw_ground(self) -> None:
        s = self._scale()
        widget = self.widget
        _, ground_y = self._origin()
        ground_y += C.GROUND_Y * s
        Color(*C.COLOR_GROUND)
        Line(points=[widget.x, ground_y, widget.right, ground_y],
             width=C.GROUND_LINE_WIDTH * s)

    def _draw_powerup(self, powerup) -> None:
        """A coloured disc with an icon on it, ringed for contrast.

        The old white highlight dot was the only marking, which made every
        power-up the same object in a different colour -- fine once you had
        memorised the palette, useless before that. The icon says what it is;
        the colour still says which one at a glance.
        """
        color = C.POWERUP_COLORS.get(powerup.kind, C.COLOR_ACCENT)
        w, h = C.POWERUP_SIZE
        ring = C.POWERUP_RING

        # Outline first, drawn as a slightly larger disc behind the real one.
        # A flat disc of colour alone sank into the painted seasons.
        Color(*_shade(color, 0.55))
        pos, size = self._place(powerup.x - ring, powerup.y - ring,
                                w + ring * 2.0, h + ring * 2.0)
        Ellipse(pos=pos, size=size)

        Color(*color)
        pos, size = self._place(powerup.x, powerup.y, w, h)
        Ellipse(pos=pos, size=size)

        # Ink chosen against the disc, so the yellow Star does not get a white
        # icon on it that nobody can read.
        luma = 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
        Color(*(C.COLOR_ICON_DARK if luma > C.ICON_DARK_ABOVE
                else C.COLOR_ICON_LIGHT))
        for part in C.POWERUP_ICONS.get(powerup.kind, ()):
            self._draw_icon(part, powerup.x, powerup.y, w, h)

    def _draw_icon(self, part: dict, ox: float, oy: float,
                   w: float, h: float) -> None:
        """One icon primitive, in coordinates normalised to a (w, h) box."""
        kind = part["k"]
        if kind == "tri":
            p = part["p"]
            points: list[float] = []
            for i in range(0, 6, 2):
                pos, _ = self._place(ox + p[i] * w, oy + p[i + 1] * h, 0.0, 0.0)
                points.extend(pos)
            Triangle(points=points)
        elif kind == "ring":
            cx, cy, radius, stroke = part["c"]
            pos, _ = self._place(ox + cx * w, oy + cy * h, 0.0, 0.0)
            scale = self._scale()
            Line(circle=(pos[0], pos[1], radius * w * scale),
                 width=max(1.0, stroke * w * scale))
        elif kind == "star":
            cx, cy, outer, inner, spikes = part["s"]
            pos, _ = self._place(ox + cx * w, oy + cy * h, 0.0, 0.0)
            self._draw_star(pos, outer * w * self._scale(),
                            inner * w * self._scale(), int(spikes))
        else:
            x, y, pw, ph = part["r"]
            pos, size = self._place(ox + x * w, oy + y * h, pw * w, ph * h)
            (Ellipse if kind == "ellipse" else Rectangle)(pos=pos, size=size)

    @staticmethod
    def _draw_star(centre: tuple[float, float], outer: float, inner: float,
                   spikes: int) -> None:
        """A filled star, as a fan of triangles around its centre.

        Kivy has no filled-polygon instruction, so the shape is built from
        Triangles -- one per edge of the star's outline.
        """
        points = []
        for i in range(spikes * 2):
            radius = outer if i % 2 == 0 else inner
            angle = math.pi * 0.5 + i * math.pi / spikes  # first spike points up
            points.append((centre[0] + radius * math.cos(angle),
                           centre[1] + radius * math.sin(angle)))
        for i, point in enumerate(points):
            nxt = points[(i + 1) % len(points)]
            Triangle(points=[centre[0], centre[1], point[0], point[1],
                             nxt[0], nxt[1]])

    def _draw_rope(self, state: GameState) -> None:
        """Draw each rope segment, sagging when slack and taut when stretched."""
        for i in range(len(state.players) - 1):
            left, right = state.players[i], state.players[i + 1]
            tension = rope_tension(state.players, i)

            ax = left.x + C.PLAYER_WIDTH * 0.5
            ay = left.y + left.hitbox().h * 0.5
            bx = right.x + C.PLAYER_WIDTH * 0.5
            by = right.y + right.hitbox().h * 0.5

            # Slack rope droops; the droop is pulled out as tension rises.
            sag = C.ROPE_SAG_MAX * (1.0 - tension)
            # ...but never let the droop sink through the ground line, which it
            # otherwise does whenever both dinos are standing still.
            midpoint = (ay + by) * 0.5
            sag = min(sag, max(0.0, midpoint - C.ROPE_MIN_CLEARANCE))

            points: list[float] = []
            for step in range(C.ROPE_SEGMENTS + 1):
                u = step / C.ROPE_SEGMENTS
                x = ax + (bx - ax) * u
                y = ay + (by - ay) * u
                y -= 4.0 * sag * u * (1.0 - u)  # parabolic approx of a catenary
                pos, _ = self._place(x, y, 0.0, 0.0)
                points.extend(pos)

            slack_c, taut_c = C.COLOR_ROPE, C.COLOR_ROPE_TAUT
            Color(*[slack_c[j] + (taut_c[j] - slack_c[j]) * tension
                    for j in range(4)])
            width = C.ROPE_WIDTH + (C.ROPE_WIDTH_TAUT - C.ROPE_WIDTH) * tension
            Line(points=points, width=max(1.0, width * self._scale()))

    def _draw_player(self, player: Player, state: GameState) -> None:
        """Draw one character inside its hitbox.

        Every part is given in coordinates normalised to the hitbox, so the
        silhouette is confined to exactly the box the player collides with --
        the choice of character can never change anyone's hitbox -- and ducking
        squashes the whole creature for free, because the box itself shrinks.
        """
        box = player.hitbox()
        skin = C.SKINS[player.skin % len(C.SKINS)]
        base = skin["color"] if player.alive else C.COLOR_DANGER

        # A partner whose state stream has gone quiet is drawn faded. Without
        # it, a dino frozen by a lost connection is indistinguishable from a
        # dino whose owner is simply standing still -- and the two call for
        # opposite reactions from the player holding the other end of the rope.
        # The rope has already gone slack for them (see physics), so this is
        # what explains why.
        fade = 1.0 if player.connected else C.DISCONNECTED_FADE

        for part in skin["parts"]:
            Color(*_shade(base, part.get("s", 1.0) * fade))
            if part["k"] == "tri":
                p = part["p"]
                points: list[float] = []
                for i in range(0, 6, 2):
                    points.extend(self._pt(box, p[i], p[i + 1]))
                Triangle(points=points)
            else:
                x, y, w, h = part["r"]
                pos, size = self._place(box.x + x * box.w, box.y + y * box.h,
                                        w * box.w, h * box.h)
                (Ellipse if part["k"] == "ellipse" else Rectangle)(
                    pos=pos, size=size)

        # A shield covers the whole team, so ring every dino.
        if state.shield:
            pad = C.SHIELD_RING_PAD
            Color(*C.POWERUP_COLORS[C.POWERUP_SHIELD])
            pos, size = self._place(box.x - pad, box.y - pad,
                                    box.w + pad * 2.0, box.h + pad * 2.0)
            Line(rectangle=(pos[0], pos[1], size[0], size[1]), width=1.6)

        # Eye, placed per character; they all face right, into the obstacles.
        ex, ey = skin["eye"]
        pos, size = self._place(box.x + ex * box.w - C.EYE_SIZE * 0.5,
                                box.y + ey * box.h - C.EYE_SIZE * 0.5,
                                C.EYE_SIZE, C.EYE_SIZE)
        Color(*C.COLOR_EYE)
        Ellipse(pos=pos, size=size)
        Color(0.12, 0.12, 0.12, 1.0)
        pos, size = self._place(box.x + ex * box.w - C.EYE_SIZE * 0.18,
                                box.y + ey * box.h - C.EYE_SIZE * 0.18,
                                C.EYE_SIZE * 0.42, C.EYE_SIZE * 0.42)
        Ellipse(pos=pos, size=size)

    def _pt(self, box, fx: float, fy: float) -> tuple[float, float]:
        """A hitbox-normalised point in widget coordinates."""
        pos, _ = self._place(box.x + fx * box.w, box.y + fy * box.h, 0.0, 0.0)
        return pos

    def _draw_particles(self) -> None:
        for p in self._particles:
            fade = max(0.0, p.life / C.PARTICLE_LIFETIME)
            Color(p.color[0], p.color[1], p.color[2], fade)
            pos, size = self._place(p.x, p.y, C.PARTICLE_SIZE, C.PARTICLE_SIZE)
            Ellipse(pos=pos, size=size)

    # -- helpers for the HUD ------------------------------------------------

    def player_screen_pos(self, player: Player) -> tuple[float, float]:
        """Top-centre of a dino in widget coordinates, for nameplates."""
        box = player.hitbox()
        pos, size = self._place(box.x, box.y + box.h + C.NAMEPLATE_OFFSET,
                                box.w, 0.0)
        return (pos[0] + size[0] * 0.5, pos[1])
