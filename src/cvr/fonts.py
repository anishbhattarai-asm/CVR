"""Finding a monospace font, and measuring text with it."""

import functools
import logging
import os
import platform
from typing import List, Optional

from PIL import ImageFont

log = logging.getLogger(__name__)


def font_candidates() -> List[str]:
    """Monospace fonts worth trying on this OS, best first."""
    sys_name = platform.system()
    if sys_name == "Windows":
        return [
            r"C:\Windows\Fonts\CascadiaCode.ttf",
            r"C:\Windows\Fonts\CascadiaCode-Regular.ttf",
            r"C:\Windows\Fonts\CascadiaMono.ttf",
            r"C:\Windows\Fonts\consola.ttf",
            r"C:\Windows\Fonts\cour.ttf",
            r"C:\Windows\Fonts\lucon.ttf",
        ]
    if sys_name == "Darwin":
        return [
            "/Library/Fonts/Courier New.ttf",
            "/System/Library/Fonts/Monaco.ttf",
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/SFMono-Regular.otf",
        ]
    return [
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf",
    ]


def _open(path: str, size: int) -> ImageFont.FreeTypeFont:
    kwargs = {"index": 0} if path.lower().endswith(".ttc") else {}
    return ImageFont.truetype(path, size, **kwargs)


@functools.lru_cache(maxsize=16)
def _font_file(font_path: Optional[str]) -> Optional[str]:
    """First font file that actually opens, `font_path` first.

    Cached because the last resort walks the whole Windows font folder.
    """
    candidates = ([font_path] if font_path else []) + font_candidates()
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            _open(path, 12)
            return path
        except (IOError, OSError):
            continue

    fonts_dir = r"C:\Windows\Fonts"
    if platform.system() == "Windows" and os.path.exists(fonts_dir):
        for fname in os.listdir(fonts_dir):
            if fname.lower().endswith((".ttf", ".ttc")):
                path = os.path.join(fonts_dir, fname)
                try:
                    _open(path, 12)
                    return path
                except (IOError, OSError):
                    continue
    return None


def load_font(font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
    """A TrueType font at `size` px: `font_path` if it opens, else a system mono.

    Each call returns a fresh font object, so the GUI thread and a render
    thread never share one FreeType face.
    """
    if font_path and not os.path.exists(font_path):
        log.warning("Font %s not found; using a system font.", font_path)
    path = _font_file(font_path)
    if path:
        try:
            return _open(path, size)
        except (IOError, OSError):
            pass
    log.warning("No TrueType font found; falling back to bitmap default.")
    return ImageFont.load_default()


_TW_CACHE: dict = {}


def text_width(font, text: str) -> float:
    """Advance width of `text` in pixels.

    `getbbox` returns *ink* extents, which drift from the real pen advance and
    would slowly push the cursor away from the text it follows; `getlength`
    is the advance, so widths stay additive across tokens.
    """
    if not text:
        return 0.0
    key = (font, text)
    width = _TW_CACHE.get(key)
    if width is None:
        try:
            width = float(font.getlength(text))
        except (AttributeError, TypeError):
            bb = font.getbbox(text)
            width = float(bb[2] - bb[0])
        if len(_TW_CACHE) > 100_000:
            _TW_CACHE.clear()
        _TW_CACHE[key] = width
    return width


def text_height(font, text: str = "Mg") -> int:
    """Ink height of `text` in pixels."""
    bb = font.getbbox(text)
    return bb[3] - bb[1]
