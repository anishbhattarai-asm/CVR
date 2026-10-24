# CVR

Turn a source file into a typewriter style video, rendered in your real VS Code colour theme.

![A Python file being typed out in the Dark 2026 theme](https://raw.githubusercontent.com/anishbhattarai-asm/CVR/main/docs/demo.gif)

CVR reads the themes VS Code has installed on your machine, resolves their colours the same way
VS Code does, and paints every frame with them. The result is a video that looks like your editor,
not like an approximation of it.

Use it three ways: a desktop app with a live preview, a `cvr` command for scripts and batch jobs,
or a Python library you can build on.

## Features

* Reads installed VS Code themes straight from disk. Every built in theme plus anything a theme
  extension contributes, or any theme json you point it at.
* Colours resolved from the theme json, including the `include` chain and TextMate scope
  precedence, so `keyword.control`, `entity.name.function` and the rest land on the exact values
  VS Code uses.
* Syntax highlighting through Pygments for any language it can lex.
* Editor tab across the top showing the file name, drawn in the theme tab colours.
* Fixed target duration. Ask for 30 seconds and the video is 30 seconds, no matter how long the
  file is.
* Instant indentation, the way an editor indents, instead of typing leading spaces one at a time.
* Line numbers, cursor blink, smooth scrolling that follows the cursor, and a scrollbar.
* Exact theme colours in the output, with ffmpeg bundled so there is nothing else to install.
* Frames available as numpy arrays, so you can make GIFs or feed your own pipeline.

## Install

Python 3.9 or newer.

```
pip install pycvr
```

The package is called `pycvr` on PyPI; you import it as `cvr` and run it as `cvr`.

For the latest code from GitHub instead:

```
pip install git+https://github.com/anishbhattarai-asm/CVR
```

Or from a clone, which also lets you run the app without installing:

```
git clone https://github.com/anishbhattarai-asm/CVR
cd CVR
pip install .
```

This pulls in numpy, Pillow, Pygments and `imageio-ffmpeg`, which ships an ffmpeg binary. An
ffmpeg already on your `PATH` is used first.

The app needs Tkinter. It ships with Python on Windows and macOS. On Debian or Ubuntu install it
with `sudo apt install python3-tk`. The command line tool and the library work without it.

## Quick start

### The app

```
cvr
```

`cvr-gui` does the same without a console window, and from a clone without installing,
`pip install -r requirements.txt` then `python CVR.py`.

![The CVR app with the theme list and live preview](https://raw.githubusercontent.com/anishbhattarai-asm/CVR/main/docs/app.png)

1. **File**: pick the source file and where the video should go.
2. **Theme**: choose a colour theme. The preview updates as you select.
3. **Video**: resolution, frame rate, font size, line numbers, file name tab, colour accuracy.
4. **Animation**: typing speed, cursor behaviour, scrolling, instant indentation.
5. **Duration**: set a target length, or drive it with the per character and per line delays.

Press **Start rendering**. Progress and an estimate appear at the bottom, and **Cancel** stops it.
When the render is done a notification appears in the corner; click it to play the video in the
player of your choice.

### The command line

```
cvr render app.py                                   # app_typewriter.mp4, your VS Code theme
cvr render app.py -o demo.mp4 -t "Dark Modern" -d 30
cvr render main.rs -r 1920x1080 --fps 60 --font-size 28
cvr render notes.txt --language python --style typewriter --open
cvr themes                                          # list installed themes, * marks the default
cvr themes light
```

| Option | Default | |
|---|---|---|
| `-o`, `--output PATH` | `FILE_typewriter.mp4` | Where to write the video. |
| `-t`, `--theme NAME` | the theme VS Code is set to | An installed theme's name (case does not matter) or a theme json path. |
| `-d`, `--duration SECONDS` | none | Fixed length. Without it, the delays decide. |
| `-r`, `--resolution WxH` | `1280x720` | Frame size. |
| `--fps N` | `30` | Frames per second. |
| `--font PATH`, `--font-size PX` | system monospace, `18` | Font file and size. |
| `--style` | `smooth` | `smooth`, `fast`, or `typewriter` (slower, underline cursor). |
| `--char-delay`, `--line-delay` | `0.05`, `0.30` | Seconds after each character and each line. |
| `--language NAME` | from the file name | Pygments lexer name. |
| `--no-line-numbers`, `--no-tab`, `--no-cursor`, `--no-blink` | | Hide parts of the editor. |
| `--no-follow`, `--no-smooth-scroll` | | Change how the view scrolls. |
| `--no-highlight` | | Plain text. |
| `--type-indent` | | Type leading spaces instead of indenting instantly. |
| `--compatible` | | Video Windows Media Player can open. See [Colour accuracy](#colour-accuracy). |
| `--open` | | Play the video when it is done. |
| `-q`, `--quiet` / `-v`, `--verbose` | | No progress bar / print timing details. |

`python -m cvr` works anywhere `cvr` does.

### Python

```python
import cvr

cvr.render("app.py", "app.mp4", theme="Monokai", duration=20)
```

## Python API

### `cvr.render()`

```python
cvr.render(code_path=None, output_path=None, *, theme=None, duration=None,
           source=None, language=None, progress=None, cancel=None, **options) -> str
```

Renders and returns the path of the video.

* `theme` is an installed theme's name, a theme json path, a `cvr.Palette`, or `None` for the
  theme VS Code is currently set to (falling back to the bundled Dark+).
* `duration` fixes the length in seconds. Leave it out to let the delays decide.
* `source` renders a string instead of reading `code_path`. `code_path` can still be given to set
  the tab label and pick the lexer.
* `language` picks the Pygments lexer by name, such as `"python"` or `"rust"`.
* `progress(percent, total, status, eta_seconds)` is called as frames are written. `total` is
  always 100.
* `cancel` is a `threading.Event` (anything with `is_set()`). Setting it stops the render and
  deletes the partial file.
* Everything else goes to `cvr.Renderer`.

It raises `cvr.RenderError` when the render fails, `cvr.RenderCancelled` when it is cancelled,
`LookupError` for a theme that is not installed, and `FileNotFoundError` for a missing source file.

```python
import threading
import cvr

stop = threading.Event()

def show(percent, _total, status, eta):
    print(f"\r{percent:5.1f}%  {status}  ETA {eta:.0f}s", end="")

cvr.render(source='print("hello")\n', language="python", output_path="hello.mp4",
           duration=5, progress=show, cancel=stop)
```

### `cvr.Renderer`

The class behind `render()`. Construction reads and lays out the code; nothing is written until
you call `render()`.

```python
r = cvr.Renderer("app.py", "app.mp4", palette=cvr.load_theme("Dark Modern"),
                 resolution=(1920, 1080), fps=60, font_size=28)
print(r.estimated_duration(), "seconds,", r.total_frames(), "frames")
r.render()                      # returns the path, or False with the reason in r.error
```

| Option | Default | |
|---|---|---|
| `code_path` | | Source file. `str` or `pathlib.Path`. |
| `output_path` | `<name>_typewriter.mp4` in the current folder | Video to write. |
| `palette` | bundled Dark+ | A `cvr.Palette`. |
| `resolution` | `(1280, 720)` | Frame size in pixels. |
| `fps` | `30` | Frames per second. |
| `font_path` | system monospace | A TrueType font file. The default is the first one found of Cascadia Code or Consolas on Windows, Courier New or Menlo on macOS, Liberation Mono or DejaVu Sans Mono on Linux. |
| `font_size` | `18` | In pixels. |
| `char_delay` | `0.05` | Seconds after each typed character. |
| `line_delay` | `0.30` | Seconds after each line. |
| `target_duration` | `0` | Fixed length in seconds. Scales both delays and keeps their ratio. `0` turns it off. |
| `animation_type` | `"smooth"` | `"smooth"` uses the delays above. `"fast"` and `"typewriter"` replace them with presets; `"typewriter"` also draws an underline cursor. |
| `show_line_numbers` | `True` | Line number gutter. |
| `show_filename_bar` | `True` | Editor tab with the file name. |
| `show_cursor`, `cursor_blink` | `True`, `True` | The cursor, and whether it blinks. |
| `follow_cursor`, `smooth_scroll` | `True`, `True` | Scroll to keep the cursor in view, gliding rather than jumping. |
| `syntax_highlighting` | `True` | `False` draws everything in the foreground colour. |
| `instant_indent` | `True` | Indentation appears at once instead of being typed. |
| `exact_colour` | `True` | H.264 4:4:4 with untouched colours. See [Colour accuracy](#colour-accuracy). |
| `source` | `None` | Code as a string. |
| `language` | `None` | Pygments lexer name. |

### Frames

`Renderer.frames()` yields every frame in order as an RGB `uint8` numpy array of shape
`(height, width, 3)`, so you can write a GIF, add an overlay, or hand them to another library.
`create_frame(line, char)` draws a single frame on its own.

```python
from PIL import Image
import cvr

r = cvr.Renderer("app.py", palette=cvr.load_theme(), resolution=(800, 450),
                 fps=20, target_duration=10)
frames = [Image.fromarray(f) for f in r.frames()]
frames[0].save("app.gif", save_all=True, append_images=frames[1:], duration=50, loop=0)
```

[`examples/to_gif.py`](https://github.com/anishbhattarai-asm/CVR/blob/main/examples/to_gif.py) is the script that made the GIF at the top of this page.

### Themes and palettes

```python
cvr.list_themes()                 # [ThemeInfo(label, path), ...] for every installed theme
cvr.default_theme()               # the one VS Code is set to
cvr.find_theme("monokai")         # ThemeInfo or None; ignores case
cvr.load_theme("Solarized Dark")  # Palette; also takes a json path, or None for the default

pal = cvr.Palette.from_file("my-theme.json")
pal = cvr.load_theme().replace(bg="#000000", cursor="#FFCC00")
cvr.DEFAULT_PALETTE               # the bundled Dark+, no VS Code needed
```

A `Palette` holds every colour the renderer uses as an RGB tuple: `bg`, `fg`, `comment`,
`string`, `number`, `keyword_ctrl`, `storage`, `function`, `type_`, `variable`, the gutter, the
cursor, the tab colours and a few more. `Palette.FIELDS` lists them all. Any of them can be passed
as a `"#RRGGBB"` string.

Light themes are labelled with a `(light)` suffix, as in the app's list.

### Logging

The library reports through the standard `logging` module under the `cvr` logger and prints
nothing by itself. `logging.basicConfig(level=logging.INFO)` shows the timing each render settles
on.

[`examples/basic.py`](https://github.com/anishbhattarai-asm/CVR/blob/main/examples/basic.py) walks through the common cases.

## Colour accuracy

H.264 stores colour as YUV, and converting RGB to YUV and back costs a level or two on the dark
tones a code theme is built from. The **Exact theme colours** option in the Video tab (`--compatible`
on the command line, `exact_colour` in Python) controls the trade off.

**On**, the default. Colours match the preview exactly and coloured text edges stay sharp. Plays
in VLC, mpv and browsers.

**Off**. Colours land about one level off on dark tones. Plays in everything, including Windows
Media Player.

Windows Media Player cannot open H.264 4:4:4 and reports "unsupported encoding settings". Turn the
option off if you need a file it will play.

Both modes need ffmpeg, which `imageio-ffmpeg` provides. If neither it nor a system ffmpeg is
available, CVR falls back to OpenCV (`pip install "pycvr[opencv]"`), which shifts
dark tones slightly.

## How theme loading works

1. Find every VS Code extension folder, both the bundled ones next to the VS Code install and the
   ones under your user profile.
2. Read each `package.json`, collect what `contributes.themes` declares, and resolve any localised
   label through `package.nls.json`.
3. Parse the chosen theme, which is json with comments and trailing commas, and follow its
   `include` chain so a theme built on another inherits correctly.
4. Resolve TextMate scopes with VS Code rules: the longest matching selector wins, and a later
   rule beats an earlier one at the same specificity.
5. Map Pygments token types onto the resolved colours.

Identifier colouring is the one place this cannot be exact. VS Code colours identifiers with
semantic tokens from a language server, while CVR uses a lexer. Structural tokens such as
keywords, strings, numbers, comments, functions and classes match, but the odd identifier can land
on a different role.

Without VS Code installed, `cvr themes` lists only the bundled Dark+ palette, and any theme json
you have can still be loaded by path.

## Settings

The app remembers its choices in `settings.json` under `%APPDATA%\cvr` on Windows,
`~/Library/Application Support/cvr` on macOS, and `~/.config/cvr` on Linux. Set `CVR_SETTINGS` to
use another file. A `typewriter_settings.json` from an older version is read on first launch and
carried over.

## Project layout

```
src/cvr/
    __init__.py     public API
    renderer.py     Renderer, render(), timing
    palette.py      Palette and theme json parsing
    themes.py       finding installed VS Code themes
    highlight.py    Pygments tokens to theme colours
    fonts.py        font lookup and text measuring
    encoders.py     ffmpeg and OpenCV video writers
    players.py      finding installed video players
    cli.py          the cvr command
    gui/            the Tkinter app: app.py, widgets.py, settings.py
tests/              pytest suite
examples/           library examples
CVR.py              runs the app from a clone without installing
```

## Development

```
pip install -e ".[dev]"
pytest
```

The tests render small videos, so they need ffmpeg, which the install provides. Tests that need
Tkinter or OpenCV are skipped when those are missing.

## Licence

MIT. See [LICENSE](https://github.com/anishbhattarai-asm/CVR/blob/main/LICENSE).
