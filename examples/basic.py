"""Four ways to render with the cvr library. Run from anywhere:

    python examples/basic.py

Videos are written next to this file.
"""

from pathlib import Path

import cvr

HERE   = Path(__file__).parent
SAMPLE = HERE / "sample.py"

# 1. One call: the theme VS Code is set to, timing from the default delays.
print(cvr.render(SAMPLE, HERE / "sample.mp4"))

# 2. A named theme (anything `cvr themes` lists), fixed length, 1080p60.
print(cvr.render(SAMPLE, HERE / "sample_1080p.mp4",
                 theme="Dark Modern", duration=15,
                 resolution=(1920, 1080), fps=60, font_size=30))

# 3. Code from a string, with a progress line.
def progress(percent, _total, status, eta):
    print(f"\r{percent:5.1f}%  {status:<20}  ETA {eta:3.0f}s", end="", flush=True)

code = 'for word in ("typed", "by", "cvr"):\n    print(word)\n'
path = cvr.render(source=code, language="python", output_path=HERE / "hello.mp4",
                  duration=6, progress=progress)
print("\n" + path)

# 4. Your own colours: start from a theme and change what you like.
palette = cvr.load_theme().replace(name="Midnight", bg="#000000",
                                   tab_active_bg="#000000")
print(cvr.render(SAMPLE, HERE / "sample_midnight.mp4", theme=palette,
                 duration=10, animation_type="typewriter"))
