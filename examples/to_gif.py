"""Make a GIF instead of an mp4, using Renderer.frames().

    python examples/to_gif.py [SOURCE] [OUTPUT.gif]

frames() hands you each frame as an RGB numpy array, so the same approach
works for any image or video library.
"""

import sys
from pathlib import Path

from PIL import Image

import cvr

source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("sample.py")
output = Path(sys.argv[2]) if len(sys.argv) > 2 else source.with_suffix(".gif")

renderer = cvr.Renderer(source, palette=cvr.load_theme(), resolution=(800, 450),
                        fps=20, font_size=17, target_duration=12)

# A GIF frame lasts a whole number of centiseconds, hence 20 fps (5 cs).
frames = [Image.fromarray(f).quantize(colors=128, dither=Image.Dither.NONE)
          for f in renderer.frames()]
frames[0].save(output, save_all=True, append_images=frames[1:],
               duration=1000 // renderer.fps, loop=0, optimize=True)
print(output)
