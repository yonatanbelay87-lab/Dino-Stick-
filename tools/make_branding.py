"""Generate the Android launcher icon, its adaptive layers, and the presplash.

Everything here is drawn from the game's own data: the dino silhouette comes
straight out of ``SKINS``, and the backdrop is a crop of the season the game
opens on. So the icon cannot drift from what the game actually looks like --
change a character or swap the first season's art, re-run this, and the icon
follows.

    python tools/make_branding.py

Writes four files into ``dinostick/assets/``:

    icon.png        512  legacy launcher icon (Android 7 and older launchers)
    icon_fg.png     512  adaptive foreground -- dino only, transparent
    icon_bg.png     512  adaptive background -- season art, full bleed
    presplash.png   768  the splash shown while Python boots

Adaptive icons are the reason there are three: Android 8+ hands the launcher
two layers and masks them into whatever shape the device uses (circle, squircle,
teardrop), and it may parallax them independently. Anything important has to sit
inside the middle ~61% -- the rest can be cropped away on any given device.
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "dinostick"))

from game import constants as C  # noqa: E402

ASSETS = os.path.join(ROOT, "dinostick", "assets")

# Drawn this many times oversized and shrunk back down at the end. PIL's
# polygon fill has no antialiasing, and a hard-edged dino at 512px looks
# obviously homemade next to every other icon on the home screen.
SUPERSAMPLE = 4

# The character the game starts you on, over the season the game starts in.
SKIN = 0
SEASON = C.BG_SEQUENCE[0][0]

# Sticker outline around the dino, as a fraction of icon width. The Rex is dark
# grey and the jungle is dark green: without this the silhouette dissolves into
# the foliage at launcher size.
HALO = 0.022
HALO_COLOR = (250, 250, 248, 255)

# Adaptive icons only guarantee the middle 66/108 of the canvas is visible.
SAFE = 0.58


def _rgb(color, shade: float = 1.0) -> tuple[int, int, int, int]:
    """A game colour (0..1 floats, optionally shaded) as 8-bit RGBA."""
    return (
        min(255, int(color[0] * shade * 255)),
        min(255, int(color[1] * shade * 255)),
        min(255, int(color[2] * shade * 255)),
        int(color[3] * 255),
    )


def draw_dino(size: int, skin_index: int = SKIN) -> Image.Image:
    """The character on transparency, in a box of the game's own proportions.

    Mirrors Renderer._draw_player: parts are in coordinates normalised to the
    hitbox with y measured UP, so every one of them is flipped here for PIL's
    y-down canvas. Nothing about the character is duplicated -- only the
    projection differs.
    """
    skin = C.SKINS[skin_index % len(C.SKINS)]
    base = skin["color"]
    box_h = size
    box_w = size * (C.PLAYER_WIDTH / C.PLAYER_HEIGHT)

    layer = Image.new("RGBA", (int(round(box_w)), int(round(box_h))), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    def px(fx: float, fy: float) -> tuple[float, float]:
        return (fx * box_w, (1.0 - fy) * box_h)  # flip: game y is up

    for part in skin["parts"]:
        color = _rgb(base, part.get("s", 1.0))
        if part["k"] == "tri":
            p = part["p"]
            draw.polygon([px(p[0], p[1]), px(p[2], p[3]), px(p[4], p[5])],
                         fill=color)
        else:
            x, y, w, h = part["r"]
            left, top = px(x, y + h)
            right, bottom = px(x + w, y)
            shape = draw.ellipse if part["k"] == "ellipse" else draw.rectangle
            shape([left, top, right, bottom], fill=color)

    # The eye, sized off EYE_SIZE exactly as the renderer does.
    eye_d = C.EYE_SIZE / C.PLAYER_HEIGHT * box_h
    ex, ey = skin["eye"]
    cx, cy = px(ex, ey)
    draw.ellipse([cx - eye_d / 2, cy - eye_d / 2, cx + eye_d / 2, cy + eye_d / 2],
                 fill=_rgb(C.COLOR_EYE))
    pupil = eye_d * 0.42
    draw.ellipse([cx - pupil / 2, cy - pupil / 2, cx + pupil / 2, cy + pupil / 2],
                 fill=(31, 31, 31, 255))
    return layer


def with_halo(dino: Image.Image, halo_px: int) -> Image.Image:
    """Put a sticker outline behind the dino so it reads at launcher size."""
    if halo_px < 1:
        return dino
    pad = halo_px + 2
    padded = Image.new("RGBA", (dino.width + pad * 2, dino.height + pad * 2),
                       (0, 0, 0, 0))
    padded.paste(dino, (pad, pad))
    # Dilate the silhouette's alpha, and use that as the outline's mask.
    grown = padded.split()[3].filter(ImageFilter.MaxFilter(halo_px * 2 + 1))
    out = Image.new("RGBA", padded.size, (0, 0, 0, 0))
    out.paste(Image.new("RGBA", padded.size, HALO_COLOR), (0, 0), grown)
    out.alpha_composite(padded)
    return out


def season_crop(size: int, zoom: float = 1.0, focus: float = 0.42
                ) -> Image.Image:
    """A square of the opening season's art, ground band included.

    ``focus`` picks how far along the strip to crop from; it is chosen to land
    on a stretch with a palm tree and a mountain rather than flat canopy.
    """
    source = Image.open(os.path.join(ASSETS, C.BG_DIR_NAME, SEASON)).convert("RGB")
    side = int(source.height / zoom)
    left = int((source.width - side) * focus)
    top = source.height - side
    return source.crop((left, top, left + side, top + side)).resize(
        (size, size), Image.LANCZOS)


def _shade_bottom(image: Image.Image, strength: float = 0.30) -> None:
    """Darken towards the bottom, so a pale dino keeps its edge over the ground."""
    height = image.height
    overlay = Image.new("L", (1, height), 0)
    for y in range(height):
        t = max(0.0, (y / height - 0.45) / 0.55)
        overlay.putpixel((0, y), int(t * t * strength * 255))
    mask = overlay.resize((image.width, height))
    image.paste(Image.new("RGB", image.size, (0, 0, 0)), (0, 0), mask)


def make_icon(size: int = 512) -> Image.Image:
    """Legacy icon: the dino standing on the painted ground, full bleed."""
    s = size * SUPERSAMPLE
    canvas = season_crop(s, zoom=2.3)
    _shade_bottom(canvas)
    canvas = canvas.convert("RGBA")

    dino = with_halo(draw_dino(int(s * 0.46)), int(s * HALO))
    # Stand it on the season's own ground line rather than guessing.
    ground = int(s * C.BG_GROUND_FRACTIONS[SEASON])
    canvas.alpha_composite(dino, ((s - dino.width) // 2,
                                  s - ground - dino.height))
    return canvas.resize((size, size), Image.LANCZOS)


def make_adaptive_background(size: int = 512) -> Image.Image:
    s = size * SUPERSAMPLE
    canvas = season_crop(s, zoom=2.0)
    _shade_bottom(canvas, 0.22)
    return canvas.resize((size, size), Image.LANCZOS).convert("RGBA")


def make_adaptive_foreground(size: int = 512) -> Image.Image:
    """Dino only, centred and kept inside the safe zone launchers may crop to."""
    s = size * SUPERSAMPLE
    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dino = with_halo(draw_dino(int(s * SAFE * 0.92)), int(s * HALO))
    canvas.alpha_composite(dino, ((s - dino.width) // 2, (s - dino.height) // 2))
    return canvas.resize((size, size), Image.LANCZOS)


def make_presplash(size: int = 768) -> Image.Image:
    s = size * SUPERSAMPLE
    canvas = season_crop(s, zoom=1.6)
    _shade_bottom(canvas, 0.22)
    canvas = canvas.convert("RGBA")
    dino = with_halo(draw_dino(int(s * 0.30)), int(s * HALO * 0.8))
    ground = int(s * C.BG_GROUND_FRACTIONS[SEASON])
    canvas.alpha_composite(dino, ((s - dino.width) // 2,
                                  s - ground - dino.height))
    return canvas.resize((size, size), Image.LANCZOS)


def main() -> None:
    outputs = {
        "icon.png": make_icon(512),
        "icon_fg.png": make_adaptive_foreground(512),
        "icon_bg.png": make_adaptive_background(512),
        "presplash.png": make_presplash(768),
    }
    for name, image in outputs.items():
        path = os.path.join(ASSETS, name)
        image.save(path)
        print(f"wrote {path}  {image.size[0]}x{image.size[1]}")


if __name__ == "__main__":
    main()
