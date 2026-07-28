# Fonts

Drop three `.ttf` files in **this folder**. The game reads them from disk at
startup and never downloads anything — see `ui/fonts.py`.

**The game runs fine without them.** Every role falls back to the Roboto that
ships inside Kivy itself, logs one warning, and starts normally. You only lose
the arcade look, never the app.

## Exactly what to download

Download once, on a machine with internet, then copy the files here. All three
are free (SIL Open Font License) and can be shipped inside your APK.

| Save it as | Family | Where |
|---|---|---|
| `Fredoka-Bold.ttf` | Fredoka, weight **Bold (700)** | fonts.google.com/specimen/Fredoka |
| `Nunito-SemiBold.ttf` | Nunito, weight **SemiBold (600)** | fonts.google.com/specimen/Nunito |
| `SpaceMono-Bold.ttf` | Space Mono, weight **Bold (700)** | fonts.google.com/specimen/Space+Mono |

Google Fonts hands you a `.zip`. Fredoka and Nunito are **variable** fonts, so
the zip's top level holds `Fredoka[wdth,wght].ttf` — that is *not* the file you
want. Open the **`static/`** folder inside the zip and take the single-weight
file from there, then rename it to match the table above.

### Alternatives the loader also accepts

If you would rather use a different family, these filenames are recognised
without touching any code — first match wins:

- **Display**: `Fredoka-Bold.ttf`, `Fredoka-SemiBold.ttf`, `Baloo2-Bold.ttf`,
  `BalooBhai2-Bold.ttf`
- **Body**: `Nunito-SemiBold.ttf`, `Nunito-Bold.ttf`, `Roboto-Medium.ttf`
- **Mono**: `SpaceMono-Bold.ttf`, `SpaceMono-Regular.ttf`,
  `JetBrainsMono-Bold.ttf`, `RobotoMono-Bold.ttf`

To use something else entirely, add its filename to `ROLES` in `ui/fonts.py`.

## How they are used

| Role | Registered name | Used for |
|---|---|---|
| Display | `"Display"` | The DINO STICK wordmark, screen titles, every button, big numbers |
| Body | `"Body"` | Sentences: taglines, hints, player names, status lines |
| Mono | `"Mono"` | The host address, where `1`/`l` and `0`/`O` must not be confusable |

In code you ask for the role, never the file:

```python
Label(text="PLAY", font_name=theme.FONT_DISPLAY_NAME)   # -> "Display"
```

## Checking it worked

On startup the log prints one of these:

```
[INFO ] fonts: bundled: Display, Body, Mono          <- all three found
[INFO ] fonts: bundled: Display; fallback: Body, Mono <- some missing
[WARNING] fonts: falling back to Kivy's built-in Roboto for: Body (wanted Nunito-SemiBold.ttf), ...
```

## Packaging

`buildozer.spec` already ships them: `ttf` is in `source.include_exts` and
`assets/*/*` is in `source.include_patterns`, which covers `assets/fonts/`.
Nothing to change when you add the files.
