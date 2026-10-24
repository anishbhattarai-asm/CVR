"""CVR: turn source code into a typewriter video in your real VS Code theme.

    import cvr
    cvr.render("script.py", "script.mp4", theme="Dark Modern", duration=20)

The GUI lives in `cvr.gui` and is only imported when you ask for it, so the
library works on machines without Tk.
"""

__version__ = "0.1.0"

from .palette import DEFAULT_PALETTE, Palette
from .renderer import (ANIMATION_STYLES, CodeTypewriterVideoRenderer,
                       RenderCancelled, Renderer, RenderError, render)
from .themes import (ThemeInfo, active_vscode_theme_label, default_theme,
                     find_theme, list_themes, load_theme)

__all__ = [
    "ANIMATION_STYLES",
    "CodeTypewriterVideoRenderer",
    "DEFAULT_PALETTE",
    "Palette",
    "RenderCancelled",
    "RenderError",
    "Renderer",
    "ThemeInfo",
    "active_vscode_theme_label",
    "default_theme",
    "find_theme",
    "list_themes",
    "load_theme",
    "render",
    "__version__",
]
