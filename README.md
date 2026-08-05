# CVR

Turn a source file into a typewriter style video, rendered in your real VS Code colour theme.

CVR reads the themes VS Code has installed on your machine, resolves their colours the same way
VS Code does, and paints every frame with them. The result is a video that looks like your editor,
not like an approximation of it.

## Features

* Reads installed VS Code themes straight from disk. Every built in theme plus anything a theme
  extension contributes, all listed in a picker with a live preview.
* Colours resolved from the theme json, including the `include` chain and TextMate scope
  precedence, so `keyword.control`, `entity.name.function` and the rest land on the exact values
  VS Code uses.
* Syntax highlighting through Pygments for any language it can lex.
* Editor tab across the top showing the file name, drawn in the theme tab colours.
* Fixed target duration. Ask for 30 seconds and the video is 30 seconds, no matter how long the
  file is.
* Instant indentation, the way an editor indents, instead of typing leading spaces one at a time.
* Line numbers, cursor blink, smooth scrolling that follows the cursor, and a scrollbar.
* Live preview that fills the panel and repaints as you change settings.
* Cancel a render in progress. Partial files are cleaned up.

## Requirements

Python 3.9 or newer.

```
pip install opencv-python pillow numpy pygments
```

Tkinter ships with Python on Windows and macOS. On Debian or Ubuntu install it with
`sudo apt install python3-tk`.

ffmpeg is optional but recommended. With it on your PATH, CVR encodes through ffmpeg and keeps
theme colours intact. Without it, CVR falls back to the OpenCV writer, which shifts dark tones
slightly.

## Usage

```
python CVR.py
```

1. **File**: pick the source file and where the video should go.
2. **Theme**: choose a colour theme. The preview updates as you select.
3. **Video**: resolution, frame rate, font size, line numbers, file name tab, colour accuracy.
4. **Animation**: typing speed, cursor behaviour, scrolling, instant indentation.
5. **Duration**: set a target length, or drive it with the per character and per line delays.

Press **Start rendering**. Progress and an estimate appear at the bottom, and **Cancel** stops it.

## Colour accuracy

H.264 stores colour as YUV, and converting RGB to YUV and back costs a level or two on the dark
tones a code theme is built from. The **Exact theme colours** option in the Video tab controls the
trade off.

**On**, the default. Colours match the preview exactly and coloured text edges stay sharp. Plays
in VLC, mpv and browsers.

**Off**. Colours land about one level off on dark tones. Plays in everything, including Windows
Media Player.

Windows Media Player cannot open H.264 4:4:4 and reports "unsupported encoding settings". Turn the
option off if you need a file it will play.

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

## Settings

Choices are written to `typewriter_settings.json` next to the script and restored on the next
launch.

## Licence

MIT. See [LICENSE](LICENSE).
