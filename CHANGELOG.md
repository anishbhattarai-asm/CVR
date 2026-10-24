# Changelog

## 0.1.0

First release as an installable package. The rendered frames are pixel for pixel what the single
`CVR.py` produced, apart from the line number and highlighting changes below.

### Added

* `pip install` support, with a `cvr` command line tool and a `cvr-gui` launcher.
* A Python API: `cvr.render()`, `cvr.Renderer`, `cvr.load_theme()`, `cvr.list_themes()` and
  `cvr.Palette`.
* `Renderer.frames()` yields each frame as a numpy array, for GIFs or your own pipeline.
* Render from a string with `source=` and pick the lexer with `language=`.
* `Palette.replace()` and hex strings for custom colours.
* Tests and a GitHub Actions workflow.

### Changed

* The code is split into modules under `src/cvr/`. `python CVR.py` still opens the app.
* ffmpeg now comes with the package through `imageio-ffmpeg`, so exact theme colours work without
  installing anything else. OpenCV is optional (`pip install code-video-renderer[opencv]`).
* App settings live in the user config folder (`%APPDATA%\cvr`, `~/Library/Application Support/cvr`
  or `~/.config/cvr`). An existing `typewriter_settings.json` is picked up on first launch.
* Line numbers appear as each line is typed, instead of the whole gutter being numbered from
  the first frame.
* The preview uses the same highlighter as the render, so their colours agree.
* A failed render shows the reason in the app instead of pointing at the console.
* A failed or interrupted render deletes its partial file.

### Fixed

* A file that started with blank lines was highlighted one line out, because Pygments dropped the
  leading newlines.
* Text the highlighter could not place fell back to Dark+ grey instead of the theme's foreground.
