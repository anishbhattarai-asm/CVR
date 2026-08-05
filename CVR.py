
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk
import textwrap
import math
import re
import os
import platform
import subprocess
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from typing import List, Tuple, Optional
import json

try:
    from pygments import lex
    from pygments.lexers import get_lexer_for_filename, guess_lexer, TextLexer
    from pygments.token import Token
    PYGMENTS_OK = True
except ImportError:
    PYGMENTS_OK = False
    print("WARNING: pygments not installed.  pip install pygments")

TAB_WIDTH           = 4      # PIL draws "\t" as one blank glyph, so expand it
PREVIEW_CHAR_LIMIT  = 3000
PREVIEW_MAX_LINES   = 18
PREVIEW_SCALE       = 0.30
PREVIEW_MAX_W       = 600
PREVIEW_MAX_H       = 340
FINAL_PAUSE_SECONDS = 2.0
SETTINGS_FILE       = os.path.join(                 # next to this script, so
    os.path.dirname(os.path.abspath(__file__)),     # settings survive being
    "typewriter_settings.json")                     # launched from anywhere

def _h(hex_str: str) -> Tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

C_PLAIN       = _h("#D4D4D4")
C_COMMENT     = _h("#6A9955")
C_STRING      = _h("#CE9178")
C_NUMBER      = _h("#B5CEA8")
C_PURPLE      = _h("#C586C0")
C_BLUE        = _h("#569CD6")
C_YELLOW      = _h("#DCDCAA")
C_TEAL        = _h("#4EC9B0")
C_LIGHT_BLUE  = _h("#9CDCFE")
C_BRIGHT_BLUE = _h("#4FC1FF")

VSCODE_BG            = _h("#1E1E1E")
VSCODE_GUTTER_FG     = _h("#858585")
VSCODE_GUTTER_ACTIVE = _h("#C6C6C6")
VSCODE_GUTTER_BORDER = _h("#404040")
VSCODE_CURSOR        = _h("#AEAFAD")

class Palette:
    """Every colour the renderer draws with, as RGB tuples.

    Defaults reproduce VS Code Dark+; `from_vscode_theme` replaces them with
    the real values out of an installed theme.
    """

    FIELDS = (
        "bg", "fg", "gutter_fg", "gutter_active", "gutter_border", "cursor",
        "comment", "string", "number", "constant", "other_constant",
        "keyword_ctrl", "storage", "operator", "operator_word",
        "function", "builtin_fn", "type_", "namespace", "variable",
        "language_var", "decorator",
        "tab_bg", "tab_active_bg", "tab_active_fg", "tab_border",
        "tab_accent",
    )

    def __init__(self, name: str = "Bundled Dark Plus", **kw):
        self.name = name
        values = dict(
            bg            = VSCODE_BG,
            fg            = C_PLAIN,
            gutter_fg     = VSCODE_GUTTER_FG,
            gutter_active = VSCODE_GUTTER_ACTIVE,
            gutter_border = VSCODE_GUTTER_BORDER,
            cursor        = VSCODE_CURSOR,
            comment       = C_COMMENT,
            string        = C_STRING,
            number        = C_NUMBER,
            constant      = C_BLUE,
            other_constant= C_BRIGHT_BLUE,
            keyword_ctrl  = C_PURPLE,
            storage       = C_BLUE,
            operator      = C_PLAIN,
            operator_word = C_BLUE,
            function      = C_YELLOW,
            builtin_fn    = C_YELLOW,
            type_         = C_TEAL,
            namespace     = C_TEAL,
            variable      = C_LIGHT_BLUE,
            language_var  = C_BLUE,
            decorator     = C_YELLOW,
            tab_bg        = _h("#252526"),
            tab_active_bg = VSCODE_BG,
            tab_active_fg = _h("#FFFFFF"),
            tab_border    = _h("#2A2B2C"),
            tab_accent    = _h("#0078D4"),
        )
        values.update({k: v for k, v in kw.items() if v is not None})
        for field in self.FIELDS:
            setattr(self, field, values[field])

    # VS Code theme loading

    @staticmethod
    def _strip_jsonc(text: str) -> str:
        """Theme files are JSON with comments and trailing commas."""
        out, i, n, in_str = [], 0, len(text), False
        while i < n:
            c = text[i]
            if in_str:
                out.append(c)
                if c == "\\" and i + 1 < n:
                    out.append(text[i + 1]); i += 2; continue
                if c == '"':
                    in_str = False
                i += 1; continue
            if c == '"':
                in_str = True; out.append(c); i += 1; continue
            if c == "/" and i + 1 < n:
                if text[i + 1] == "/":
                    while i < n and text[i] != "\n":
                        i += 1
                    continue
                if text[i + 1] == "*":
                    i += 2
                    while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                        i += 1
                    i += 2; continue
            out.append(c); i += 1
        return re.sub(r",(\s*[}\]])", r"\1", "".join(out))

    @classmethod
    def _read_theme(cls, path: str, depth: int = 0):
        """Return (colors, tokenColors) with any `include` chain resolved.

        An included theme is the *base*: its rules come first so the including
        theme's rules win, which is how VS Code resolves them.
        """
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            data = json.loads(cls._strip_jsonc(fh.read()))

        colors, rules = {}, []
        include = data.get("include")
        if include and depth < 8:
            base = os.path.normpath(os.path.join(os.path.dirname(path), include))
            if os.path.isfile(base):
                colors, rules = cls._read_theme(base, depth + 1)

        colors = {**colors, **data.get("colors", {})}
        return colors, rules + list(data.get("tokenColors", []))

    @staticmethod
    def _resolve(rules: List[dict], scope: str) -> Optional[str]:
        """Longest matching TextMate selector wins; ties go to the later rule."""
        best, best_len = None, -1
        for rule in rules:
            selectors = rule.get("scope", [])
            if isinstance(selectors, str):
                selectors = selectors.split(",")
            for sel in selectors:
                sel = sel.strip()
                # Descendant selectors ("source.cpp keyword") only apply in
                # context we don't model, so score them by their last part.
                sel = sel.split()[-1] if sel else sel
                if sel and (scope == sel or scope.startswith(sel + ".")):
                    if len(sel) >= best_len:
                        fg = (rule.get("settings") or {}).get("foreground")
                        if fg:
                            best, best_len = fg, len(sel)
        return best

    @classmethod
    def from_vscode_theme(cls, label: str, path: str) -> "Palette":
        colors, rules = cls._read_theme(path)

        def col(key, fallback=None):
            v = colors.get(key)
            return _h(v) if isinstance(v, str) and len(v.lstrip("#")) >= 6 else fallback

        def tok(scope, fallback=None):
            v = cls._resolve(rules, scope)
            return _h(v) if isinstance(v, str) and len(v.lstrip("#")) >= 6 else fallback

        fg = col("editor.foreground") or tok("source") or C_PLAIN
        return cls(
            name          = label,
            bg            = col("editor.background"),
            fg            = fg,
            gutter_fg     = col("editorLineNumber.foreground"),
            gutter_active = col("editorLineNumber.activeForeground"),
            gutter_border = col("editorIndentGuide.background1",
                                col("editorIndentGuide.background")),
            cursor        = col("editorCursor.foreground", fg),
            comment       = tok("comment"),
            string        = tok("string"),
            number        = tok("constant.numeric"),
            constant      = tok("constant.language"),
            other_constant= tok("variable.other.constant", tok("constant.other")),
            keyword_ctrl  = tok("keyword.control", tok("keyword")),
            storage       = tok("storage.type", tok("keyword")),
            operator      = tok("keyword.operator", fg),
            operator_word = tok("keyword.operator.logical.python",
                                tok("keyword.operator.expression")),
            function      = tok("entity.name.function"),
            builtin_fn    = tok("support.function", tok("entity.name.function")),
            type_         = tok("entity.name.type", tok("support.type")),
            namespace     = tok("entity.name.namespace", tok("entity.name.type")),
            variable      = tok("variable", fg),
            language_var  = tok("variable.language", tok("constant.language")),
            decorator     = tok("entity.name.function.decorator",
                                tok("entity.name.function")),
            tab_bg        = col("editorGroupHeader.tabsBackground",
                                col("tab.inactiveBackground")),
            tab_active_bg = col("tab.activeBackground", col("editor.background")),
            tab_active_fg = col("tab.activeForeground", fg),
            tab_border    = col("editorGroupHeader.tabsBorder", col("tab.border")),
            tab_accent    = col("tab.activeBorderTop", col("tab.activeBorder")),
        )


def _vscode_extension_roots() -> List[str]:
    """Folders that may hold VS Code extensions, both bundled and installed."""
    roots, home = [], os.path.expanduser("~")
    for name in (".vscode", ".vscode-insiders", ".vscode-server"):
        roots.append(os.path.join(home, name, "extensions"))

    installs = []
    sys_name = platform.system()
    if sys_name == "Windows":
        for base in (os.environ.get("LOCALAPPDATA", ""),
                     os.environ.get("ProgramFiles", ""),
                     os.environ.get("ProgramFiles(x86)", "")):
            if base:
                installs += [os.path.join(base, "Programs", "Microsoft VS Code"),
                             os.path.join(base, "Microsoft VS Code"),
                             os.path.join(base, "Programs", "Microsoft VS Code Insiders")]
    elif sys_name == "Darwin":
        installs.append("/Applications/Visual Studio Code.app/Contents/Resources")
    else:
        installs += ["/usr/share/code", "/opt/visual-studio-code",
                     "/usr/lib/code"]

    for install in installs:
        if not os.path.isdir(install):
            continue
        # Newer Windows builds nest resources under a folder named by build id.
        candidates = [install] + [os.path.join(install, d)
                                  for d in os.listdir(install)
                                  if os.path.isdir(os.path.join(install, d))]
        for cand in candidates:
            ext = os.path.join(cand, "resources", "app", "extensions")
            if os.path.isdir(ext):
                roots.append(ext)
    return [r for r in roots if os.path.isdir(r)]


def discover_vscode_themes() -> List[Tuple[str, str]]:
    """List of (label, path to theme json) for every colour theme found."""
    found, seen = [], set()
    for root in _vscode_extension_roots():
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            pkg_dir  = os.path.join(root, entry)
            pkg_json = os.path.join(pkg_dir, "package.json")
            if not os.path.isfile(pkg_json):
                continue
            try:
                with open(pkg_json, "r", encoding="utf-8-sig", errors="replace") as fh:
                    pkg = json.loads(Palette._strip_jsonc(fh.read()))
            except (OSError, ValueError):
                continue

            themes = (pkg.get("contributes") or {}).get("themes") or []
            if not themes:
                continue

            nls = {}
            nls_path = os.path.join(pkg_dir, "package.nls.json")
            if os.path.isfile(nls_path):
                try:
                    with open(nls_path, "r", encoding="utf-8-sig",
                              errors="replace") as fh:
                        nls = json.loads(Palette._strip_jsonc(fh.read()))
                except (OSError, ValueError):
                    nls = {}

            for theme in themes:
                label = theme.get("label") or theme.get("id") or ""
                if label.startswith("%") and label.endswith("%"):
                    entry_val = nls.get(label.strip("%"), label)
                    if isinstance(entry_val, dict):        # newer nls format
                        entry_val = entry_val.get("message", label)
                    label = entry_val
                rel = theme.get("path") or ""
                path = os.path.normpath(os.path.join(pkg_dir, rel))
                if not label or not os.path.isfile(path) or path in seen:
                    continue
                seen.add(path)
                dark = (theme.get("uiTheme") or "").startswith(("vs-dark", "hc-black"))
                found.append((f"{label}{'' if dark else '  (light)'}", path))

    found.sort(key=lambda t: t[0].lower())
    return found


def active_vscode_theme_label() -> Optional[str]:
    """`workbench.colorTheme` from the user's settings, if they set one."""
    sys_name = platform.system()
    if sys_name == "Windows":
        base = os.path.join(os.environ.get("APPDATA", ""), "Code", "User")
    elif sys_name == "Darwin":
        base = os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support", "Code", "User")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config", "Code", "User")

    candidates = [os.path.join(base, "settings.json")]
    profiles = os.path.join(base, "profiles")
    if os.path.isdir(profiles):
        for entry in os.listdir(profiles):
            candidates.append(os.path.join(profiles, entry, "settings.json"))

    label = None
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                data = json.loads(Palette._strip_jsonc(fh.read()))
        except (OSError, ValueError):
            continue
        label = data.get("workbench.colorTheme") or label
    return label


_STORAGE_KW   = frozenset({"def", "class", "lambda"})
_CONTROL_KW   = frozenset({
    "if", "elif", "else", "for", "while", "return", "yield",
    "try", "except", "finally", "raise", "with", "as",
    "pass", "break", "continue", "del", "assert",
    "global", "nonlocal", "async", "await",
})
_NAMESPACE_KW = frozenset({"import", "from"})
_CONSTANT_KW  = frozenset({"True", "False", "None"})

_TYPE_BUILTINS = frozenset({
    "int", "str", "float", "bool", "bytes", "complex",
    "dict", "list", "set", "tuple", "type", "object",
    "bytearray", "memoryview", "frozenset",
})

PYGMENTS_COLOR_MAP: List[Tuple] = [] if not PYGMENTS_OK else [
    (Token.Comment,               "comment"),
    (Token.Literal.String.Doc,    "string"),
    (Token.Literal.String,        "string"),
    (Token.Literal.Number,        "number"),
    (Token.Keyword.Constant,      "constant"),
    (Token.Keyword.Namespace,     "keyword_ctrl"),
    (Token.Keyword.Type,          "storage"),
    (Token.Keyword,               "keyword_ctrl"),
    (Token.Operator.Word,         "operator_word"),
    (Token.Operator,              "operator"),
    (Token.Punctuation,           "fg"),
    (Token.Name.Builtin.Pseudo,   "language_var"),
    (Token.Name.Builtin,          "builtin_fn"),
    (Token.Name.Class,            "type_"),
    (Token.Name.Exception,        "type_"),
    (Token.Name.Namespace,        "namespace"),
    (Token.Name.Function.Magic,   "function"),
    (Token.Name.Function,         "function"),
    (Token.Name.Decorator,        "decorator"),
    (Token.Name.Attribute,        "variable"),
    (Token.Name.Variable.Magic,   "variable"),
    (Token.Name.Variable,         "variable"),
    (Token.Name.Constant,         "other_constant"),
    (Token.Name,                  "variable"),
    (Token,                       "fg"),
]

DEFAULT_PALETTE = Palette()

def _pygments_color(ttype, pal: "Palette") -> Tuple[int, int, int]:
    for token_cls, field in PYGMENTS_COLOR_MAP:
        if ttype is token_cls or ttype in token_cls:
            return getattr(pal, field)
    return pal.fg

_TW_CACHE: dict = {}

def _tw(font, text: str) -> float:
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

def _th(font, text: str = "Mg") -> int:
    bb = font.getbbox(text)
    return bb[3] - bb[1]

class _OpenCVWriter:
    """Fallback encoder. Goes through YUV, so dark colours shift slightly."""

    def __init__(self, writer):
        self._writer = writer
        self.exact_colour = False

    @classmethod
    def open(cls, path: str, fps: int, size: Tuple[int, int]):
        fourcc_of = getattr(cv2, "VideoWriter_fourcc", None) or cv2.VideoWriter.fourcc
        for fourcc_str in ("avc1", "mp4v"):
            w = cv2.VideoWriter(path, fourcc_of(*fourcc_str), float(fps), size)
            if w.isOpened():
                return cls(w)
            w.release()
        return None

    def write(self, frame: np.ndarray):
        self._writer.write(frame)

    def release(self):
        self._writer.release()


class _FFmpegWriter:
    """Encoder that preserves colours exactly.

    OpenCV converts every frame to limited range YUV, which moves the
    theme colours. Encoding through ffmpeg keeps them intact, so what the
    palette says is what the player shows.
    """

    def __init__(self, proc):
        self._proc = proc
        self.exact_colour = True

    @staticmethod
    def _ffmpeg_exe() -> Optional[str]:
        from shutil import which
        exe = which("ffmpeg")
        if exe:
            return exe
        try:                                    # bundled with imageio/manim
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None

    @classmethod
    def open(cls, path: str, fps: int, size: Tuple[int, int],
             exact_colour: bool = False):
        exe = cls._ffmpeg_exe()
        if not exe:
            return None

        if exact_colour:
            # RGB in, RGB out: no colour conversion, no chroma subsampling.
            # Exact, but this is H.264 High 4:4:4 Predictive, which Windows
            # Media Player refuses to open. VLC and mpv play it.
            codec = ["-c:v", "libx264rgb", "-crf", "12", "-pix_fmt", "bgr0"]
        else:
            # Ordinary High profile that plays everywhere. Full range keeps
            # the luma untouched, where limited range would compress it into
            # 16..235 and lose a level on the dark tones a code theme uses.
            codec = ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
                     "-color_range", "pc", "-colorspace", "bt709",
                     "-color_primaries", "bt709", "-color_trc", "bt709",
                     "-vf", "scale=in_range=full:out_range=full"]

        cmd = [
            exe, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{size[0]}x{size[1]}", "-r", str(fps),
            "-i", "-",
            "-an", *codec,
            "-preset", "medium",
            "-movflags", "+faststart",
            path,
        ]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE)
        except OSError:
            return None
        return cls(proc)

    def write(self, frame: np.ndarray):
        self._proc.stdin.write(frame.tobytes())

    def release(self):
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self._proc.kill()


class CodeTypewriterVideoRenderer:
    def __init__(
        self,
        code_path:          str,
        output_path:        Optional[str]         = None,
        resolution:         Tuple[int, int]       = (1280, 720),
        fps:                int                   = 30,
        font_path:          Optional[str]         = None,
        font_size:          int                   = 18,
        char_delay:         float                 = 0.05,
        line_delay:         float                 = 0.30,
        show_cursor:        bool                  = True,
        cursor_blink:       bool                  = True,
        follow_cursor:      bool                  = True,
        smooth_scroll:      bool                  = True,
        syntax_highlighting:bool                  = True,
        show_line_numbers:  bool                  = True,
        animation_type:     str                   = "smooth",
        target_duration:    float                 = 0.0,
        palette:            Optional["Palette"]   = None,
        show_filename_bar:  bool                  = True,
        instant_indent:     bool                  = True,
        exact_colour:       bool                  = True,
    ):
        self.palette             = palette or DEFAULT_PALETTE
        self.show_filename_bar   = show_filename_bar
        self.instant_indent      = instant_indent
        self.exact_colour        = exact_colour
        self.code_path           = code_path
        self.syntax_highlighting = syntax_highlighting
        self.show_line_numbers   = show_line_numbers
        self.follow_cursor       = follow_cursor
        self.smooth_scroll       = smooth_scroll
        self.animation_type      = animation_type
        self.target_duration     = target_duration
        self.show_cursor         = show_cursor
        self.cursor_blink        = cursor_blink

        if output_path is None:
            desktop   = os.path.join(os.path.expanduser("~"), "Desktop")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base      = desktop if os.path.exists(desktop) else "."
            self.output_path = os.path.join(base, f"code_video_{timestamp}.mp4")
        else:
            self.output_path = output_path

        self.resolution = resolution
        self.fps        = fps
        self.font_size  = font_size
        self.padding    = 20

        delay_presets = {
            "fast":       (0.02, 0.10),
            "typewriter": (0.10, 0.50),
            "smooth":     (char_delay, line_delay),
        }
        self.char_delay, self.line_delay = delay_presets.get(
            animation_type, (char_delay, line_delay))

        pal = self.palette
        self.background_color = pal.bg
        self.text_color       = pal.fg
        self.cursor_color     = pal.cursor
        self._gutter_fg       = pal.gutter_fg
        self._gutter_active   = pal.gutter_active
        self._gutter_border   = pal.gutter_border

        self.font = self._load_font(font_path, font_size)

        _cap_bb   = self.font.getbbox("M")
        _full_bb  = self.font.getbbox("Mg")
        self.char_width     = max(1.0, _tw(self.font, "M"))
        self.char_height    = max(1, _cap_bb[3] - _cap_bb[1])
        self.line_height_px = max(1, int((_full_bb[3] - _full_bb[1]) * 1.35))

        # Editor tab across the top. Capped so a short frame keeps room
        # for the code.
        self._bar_h = (min(int(self.line_height_px * 1.9),
                           max(0, resolution[1] // 4))
                       if self.show_filename_bar else 0)
        if self._bar_h < 6:
            self._bar_h = 0
            self.show_filename_bar = False
        self._text_y_start = self._bar_h + self.padding

        self.viewport_offset = 0.0
        screen_h = max(1, resolution[1] - self._text_y_start - self.padding)
        self.max_visible_lines = max(1, screen_h // self.line_height_px)

        self.wrapped_lines:   List[str]         = []
        self.tokenized_lines: List[List[Tuple]] = []
        self.line_numbers:    List[Optional[int]]= []
        self._gutter_width    = 0
        self._text_x_start    = self.padding

        self._frame_img  = Image.new("RGB", self.resolution, self.background_color)
        self._frame_draw = ImageDraw.Draw(self._frame_img)

        self.load_code()

        if self.target_duration > 0:
            self._set_exact_duration(self.target_duration)

    def _load_font(self, font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
        candidates: List[str] = []
        if font_path:
            candidates.append(font_path)

        sys_name = platform.system()
        if sys_name == "Windows":
            candidates += [
                r"C:\Windows\Fonts\CascadiaCode.ttf",
                r"C:\Windows\Fonts\CascadiaCode-Regular.ttf",
                r"C:\Windows\Fonts\CascadiaMono.ttf",
                r"C:\Windows\Fonts\consola.ttf",
                r"C:\Windows\Fonts\cour.ttf",
                r"C:\Windows\Fonts\lucon.ttf",
            ]
        elif sys_name == "Darwin":
            candidates += [
                "/Library/Fonts/Courier New.ttf",
                "/System/Library/Fonts/Monaco.ttf",
                "/System/Library/Fonts/Menlo.ttc",
                "/System/Library/Fonts/SFMono-Regular.otf",
            ]
        else:
            candidates += [
                "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf",
            ]

        for path in candidates:
            if not path or not os.path.exists(path):
                continue
            try:
                kwargs = {"index": 0} if path.lower().endswith(".ttc") else {}
                return ImageFont.truetype(path, size, **kwargs)
            except (IOError, OSError):
                continue

        if sys_name == "Windows":
            fonts_dir = r"C:\Windows\Fonts"
            if os.path.exists(fonts_dir):
                for fname in os.listdir(fonts_dir):
                    if fname.lower().endswith((".ttf", ".ttc")):
                        try:
                            kwargs = {"index": 0} if fname.lower().endswith(".ttc") else {}
                            return ImageFont.truetype(
                                os.path.join(fonts_dir, fname), size, **kwargs)
                        except (IOError, OSError):
                            continue

        print("WARNING: No TrueType font found; falling back to bitmap default.")
        return ImageFont.load_default()

    def load_code(self):
        if not os.path.exists(self.code_path):
            raise FileNotFoundError(f"Code file not found: {self.code_path}")

        with open(self.code_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read().expandtabs(TAB_WIDTH)

        code_lines = source.splitlines(keepends=True)
        total_orig = len(code_lines)

        if self.show_line_numbers:
            sample = str(max(total_orig, 1)) + "  "
            self._gutter_width = int(math.ceil(_tw(self.font, sample)))
        else:
            self._gutter_width = 0

        sep_extra = 7 if self.show_line_numbers else 0
        usable_w  = (self.resolution[0] - 2 * self.padding
                     - self._gutter_width - sep_extra)
        self.chars_per_line = max(10, int(usable_w // self.char_width))
        self._text_x_start  = self.padding + self._gutter_width + sep_extra

        per_line_tokens = self._tokenize_source(source)

        self.wrapped_lines   = []
        self.tokenized_lines = []
        self.line_numbers    = []

        for orig_idx, raw_line in enumerate(code_lines):
            stripped    = raw_line.rstrip("\n")
            line_tokens = (per_line_tokens[orig_idx]
                           if orig_idx < len(per_line_tokens) else [])

            wrapped_texts = (
                textwrap.wrap(stripped,
                              width=self.chars_per_line,
                              replace_whitespace=False,
                              expand_tabs=False)
                or [""]
            )

            for wrap_pos, wtext in enumerate(wrapped_texts):
                self.wrapped_lines.append(wtext)
                self.tokenized_lines.append(
                    self._slice_tokens(line_tokens, wtext, stripped, wrap_pos))
                self.line_numbers.append(orig_idx + 1 if wrap_pos == 0 else None)

    def _tokenize_source(self, source: str) -> List[List[Tuple]]:
        pal = self.palette
        if not PYGMENTS_OK:
            lines = source.splitlines()
            return [[(pal.fg, ln)] for ln in lines]

        try:
            lexer = get_lexer_for_filename(self.code_path)
        except Exception:
            try:
                lexer = guess_lexer(source)
            except Exception:
                lexer = TextLexer()

        raw_tokens = list(lex(source, lexer))
        n_raw      = len(raw_tokens)

        def _next_nws(i: int) -> str:
            for k in range(i + 1, min(i + 8, n_raw)):
                t = raw_tokens[k][1]
                if t.strip():
                    return t
            return ""

        def _prev_nws(i: int) -> str:
            for k in range(i - 1, max(i - 8, -1), -1):
                t = raw_tokens[k][1]
                if t.strip():
                    return t
            return ""

        flat: List[Tuple] = []

        for idx, (ttype, ttext) in enumerate(raw_tokens):

            if ttype in Token.Comment:
                color = pal.comment

            elif ttype in Token.Literal.String:
                # Docstrings are strings in VS Code, not comments.
                color = pal.string

            elif ttype in Token.Literal.Number:
                color = pal.number

            elif ttype is Token.Keyword.Constant or ttype in Token.Keyword.Constant:
                color = pal.constant

            elif ttype is Token.Keyword.Namespace or ttype in Token.Keyword.Namespace:
                color = pal.keyword_ctrl

            elif ttype is Token.Keyword.Type or ttype in Token.Keyword.Type:
                color = pal.storage

            elif ttype is Token.Keyword or ttype in Token.Keyword:
                if ttext in _STORAGE_KW:
                    color = pal.storage
                elif ttext in _CONSTANT_KW:
                    color = pal.constant
                elif ttext in _NAMESPACE_KW or ttext in _CONTROL_KW:
                    color = pal.keyword_ctrl
                else:
                    color = pal.keyword_ctrl

            elif ttype is Token.Operator.Word:
                color = pal.operator_word

            elif ttype in Token.Operator or ttype in Token.Punctuation:
                color = pal.operator

            elif ttype is Token.Name.Builtin.Pseudo or ttype in Token.Name.Builtin.Pseudo:
                color = pal.language_var

            elif ttype is Token.Name.Builtin or ttype in Token.Name.Builtin:
                if ttext in _TYPE_BUILTINS:
                    color = pal.type_
                elif _next_nws(idx) == "(":
                    color = pal.builtin_fn
                else:
                    color = pal.language_var

            elif ttype in Token.Name.Function.Magic:
                color = pal.function
            elif ttype in Token.Name.Function:
                color = pal.function
            elif ttype in Token.Name.Class:
                color = pal.type_
            elif ttype in Token.Name.Exception:
                color = pal.type_
            elif ttype in Token.Name.Namespace:
                color = pal.namespace
            elif ttype in Token.Name.Decorator:
                color = pal.decorator
            elif ttype in Token.Name.Constant:
                color = pal.other_constant
            elif ttype in Token.Name.Attribute:
                color = pal.variable
            elif ttype in Token.Name.Variable.Magic:
                color = pal.variable
            elif ttype in Token.Name.Variable:
                color = pal.variable

            elif ttype is Token.Name or ttype in Token.Name:
                nw = _next_nws(idx)
                pw = _prev_nws(idx)
                if nw == "(":
                    color = pal.function
                elif pw == ".":
                    color = pal.variable
                elif ttext.isupper() and len(ttext) > 1 and not ttext.isdigit():
                    color = pal.other_constant
                else:
                    color = pal.variable

            else:
                color = _pygments_color(ttype, pal)

            flat.append((color, ttext))

        per_line: List[List[Tuple]] = [[]]
        for color, ttext in flat:
            parts = ttext.split("\n")
            for k, part in enumerate(parts):
                if k > 0:
                    per_line.append([])
                if part:
                    per_line[-1].append((color, part))

        return per_line

    def _slice_tokens(
        self,
        line_tokens: List[Tuple],
        wrapped_text: str,
        full_line: str,
        wrap_pos: int = 0,
    ) -> List[Tuple]:
        if not line_tokens:
            return [(C_PLAIN, wrapped_text)] if wrapped_text else []
        if not wrapped_text:
            return []

        search_start = 0
        found_idx    = -1
        for _ in range(wrap_pos + 1):
            idx = full_line.find(wrapped_text, search_start)
            if idx == -1:
                break
            found_idx    = idx
            search_start = idx + max(1, len(wrapped_text))

        if found_idx == -1:
            return [(C_PLAIN, wrapped_text)]

        start = found_idx
        end   = start + len(wrapped_text)

        result: List[Tuple] = []
        pos = 0
        for color, text in line_tokens:
            tok_end = pos + len(text)
            if tok_end > start and pos < end:
                clip_s  = max(0, start - pos)
                clip_e  = end - pos
                clipped = text[clip_s:clip_e]
                if clipped:
                    result.append((color, clipped))
            pos = tok_end
            if pos >= end:
                break

        return result or [(C_PLAIN, wrapped_text)]

    def _typed_chars(self) -> int:
        """Characters that are actually typed out.

        Leading indentation appears in one go, the way an editor indents,
        so it must not be counted in the timing either.
        """
        if self.instant_indent:
            return sum(len(l.lstrip()) for l in self.wrapped_lines)
        return sum(len(l) for l in self.wrapped_lines)

    def _set_exact_duration(self, target_seconds: float):
        total_chars = self._typed_chars()
        total_lines = len(self.wrapped_lines)
        available   = target_seconds - FINAL_PAUSE_SECONDS

        if available <= 0 or total_chars + total_lines == 0:
            return

        k           = self.line_delay / max(self.char_delay, 1e-9)
        denominator = total_chars + total_lines * k
        d           = available / denominator

        self.char_delay = max(0.0002, min(0.5, d))
        self.line_delay = max(0.0010, min(2.0, d * k))

        actual = (total_chars * self.char_delay
                  + total_lines * self.line_delay
                  + FINAL_PAUSE_SECONDS)
        print(f"[Duration] target={target_seconds:.1f}s  actual~{actual:.1f}s  "
              f"char_delay={self.char_delay:.4f}s  line_delay={self.line_delay:.4f}s")

    def estimated_duration(self) -> float:
        total_chars = self._typed_chars()
        total_lines = len(self.wrapped_lines)
        return (total_chars * self.char_delay
                + total_lines * self.line_delay
                + FINAL_PAUSE_SECONDS)

    def _desired_viewport(self, cursor_line: int) -> int:
        if not self.follow_cursor:
            return int(self.viewport_offset)
        total = len(self.wrapped_lines)
        if total <= self.max_visible_lines:
            return 0
        offset = cursor_line - (self.max_visible_lines - 3)
        return max(0, min(offset, total - self.max_visible_lines))

    def _tick_viewport(self, target: int):
        max_offset = max(0.0, float(len(self.wrapped_lines) - self.max_visible_lines))
        if not self.smooth_scroll:
            self.viewport_offset = float(max(0, min(target, int(max_offset))))
            return
        diff = float(target) - self.viewport_offset
        if abs(diff) > 0.05:
            step = diff * 0.25
            step = max(-abs(diff), min(abs(diff), step))
            self.viewport_offset += step
        else:
            self.viewport_offset = float(target)
        self.viewport_offset = max(0.0, min(self.viewport_offset, max_offset))

    def create_frame(
        self,
        cur_line:       int,
        cur_char:       int,
        cursor_visible: bool = True,
    ) -> np.ndarray:
        draw = self._frame_draw
        draw.rectangle([0, 0, *self.resolution], fill=self.background_color)

        start_line = int(self.viewport_offset)
        end_line   = min(start_line + self.max_visible_lines,
                         len(self.wrapped_lines))

        if self.show_filename_bar:
            self._draw_filename_bar(draw)

        if self.show_line_numbers and self._gutter_width > 0:
            sep_x = self.padding + self._gutter_width + 3
            draw.line(
                [(sep_x, self._text_y_start),
                 (sep_x, self.resolution[1] - self.padding)],
                fill=self._gutter_border,
                width=1,
            )

        cursor_px_x = self._text_x_start

        for i in range(start_line, end_line):
            y = self._text_y_start + (i - start_line) * self.line_height_px
            if y + self.line_height_px > self.resolution[1] - self.padding:
                break

            line   = self.wrapped_lines[i]
            tokens = self.tokenized_lines[i]

            if self.show_line_numbers and self._gutter_width > 0:
                lineno = self.line_numbers[i]
                if lineno is not None:
                    num_str   = str(lineno)
                    num_w     = _tw(self.font, num_str)
                    num_x     = self.padding + self._gutter_width - num_w - 7
                    num_color = (self._gutter_active if i == cur_line
                                 else self._gutter_fg)
                    draw.text((num_x, y), num_str, font=self.font, fill=num_color)

            x = self._text_x_start

            if i < cur_line:
                for color, tok in tokens:
                    if not self.syntax_highlighting:
                        color = self.text_color
                    draw.text((x, y), tok, font=self.font, fill=color)
                    x += _tw(self.font, tok)

            elif i == cur_line:
                drawn = 0
                for color, tok in tokens:
                    if drawn >= cur_char:
                        break
                    if not self.syntax_highlighting:
                        color = self.text_color
                    tok_len = len(tok)
                    if drawn + tok_len <= cur_char:
                        draw.text((x, y), tok, font=self.font, fill=color)
                        x     += _tw(self.font, tok)
                        drawn += tok_len
                    else:
                        partial = tok[:cur_char - drawn]
                        draw.text((x, y), partial, font=self.font, fill=color)
                        x += _tw(self.font, partial)
                        break
                cursor_px_x = x

        if (self.show_cursor and cursor_visible
                and start_line <= cur_line < end_line):
            cy = self._text_y_start + (cur_line - start_line) * self.line_height_px
            if self.animation_type == "typewriter":
                draw.rectangle(
                    [cursor_px_x,
                     cy + self.line_height_px - 3,
                     cursor_px_x + self.char_width,
                     cy + self.line_height_px - 1],
                    fill=self.cursor_color,
                )
            else:
                draw.rectangle(
                    [cursor_px_x,
                     cy,
                     cursor_px_x + 2,
                     cy + self.line_height_px - 2],
                    fill=self.cursor_color,
                )

        if len(self.wrapped_lines) > self.max_visible_lines:
            self._draw_scrollbar(draw, start_line)

        return cv2.cvtColor(np.array(self._frame_img), cv2.COLOR_RGB2BGR)

    def _draw_filename_bar(self, draw: ImageDraw.Draw):
        """The editor tab across the top, in the theme's own tab colours."""
        pal   = self.palette
        h     = self._bar_h
        width = self.resolution[0]

        draw.rectangle([0, 0, width, h], fill=pal.tab_bg)

        name  = os.path.basename(self.code_path) or "untitled"
        pad_x = max(12, int(self.char_width * 1.5))
        tab_w = int(_tw(self.font, name) + pad_x * 2)

        draw.rectangle([0, 0, tab_w, h], fill=pal.tab_active_bg)
        accent_h = max(2, int(h * 0.07))
        draw.rectangle([0, 0, tab_w, accent_h], fill=pal.tab_accent)
        draw.line([(tab_w, 0), (tab_w, h - 1)], fill=pal.tab_border)
        draw.line([(0, h - 1), (width, h - 1)], fill=pal.tab_border)

        text_y = accent_h + max(0, (h - accent_h - self.line_height_px) // 2)
        draw.text((pad_x, text_y), name, font=self.font, fill=pal.tab_active_fg)

    def _draw_scrollbar(self, draw: ImageDraw.Draw, start_line: int):
        sw      = 6
        sx      = self.resolution[0] - self.padding // 2 - sw
        total   = len(self.wrapped_lines)
        visible = self.max_visible_lines
        top     = self._text_y_start
        track_h = self.resolution[1] - top - self.padding
        if track_h < 8 or sx <= 0:
            return

        thumb_h = max(1, min(track_h, int(track_h * visible / total)))
        ratio   = start_line / max(1, total - visible)
        thumb_y = top + int((track_h - thumb_h) * ratio)

        draw.rectangle(
            [sx, top, sx + sw, self.resolution[1] - self.padding],
            fill=(50, 50, 55))
        draw.rectangle(
            [sx, thumb_y, sx + sw, thumb_y + thumb_h],
            fill=(110, 110, 120))

    def total_frames(self) -> int:
        return max(1, int(round(self.estimated_duration() * self.fps)))

    def render(
        self,
        progress_callback=None,
        cancel_flag: Optional[threading.Event] = None,
    ) -> "str | bool":
        if progress_callback:
            progress_callback(0, 100, "Initialising…", 0)

        out_dir = os.path.dirname(os.path.abspath(self.output_path))
        if out_dir and not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as exc:
                if progress_callback:
                    progress_callback(0, 100, f"Error: {exc}", 0)
                return False

        writer = _FFmpegWriter.open(self.output_path, self.fps, self.resolution,
                                    exact_colour=self.exact_colour)
        if writer is None:
            writer = _OpenCVWriter.open(self.output_path, self.fps, self.resolution)

        if writer is None:
            if progress_callback:
                progress_callback(0, 100, "Error: cannot open video writer", 0)
            return False

        blink_period = max(1, self.fps // 3)

        n_total    = self.total_frames()
        n_written  = 0
        cur_line   = 0
        cur_char   = 0
        frame_debt = 0.0
        self.viewport_offset = 0.0

        start_time = time.time()

        def cancelled() -> bool:
            return cancel_flag is not None and cancel_flag.is_set()

        def frames_for(seconds: float) -> int:
            """Whole frames owed for `seconds`, carrying the remainder forward.

            Keeps the real duration equal to the requested one even when a
            single char/line delay is shorter than one frame.
            """
            nonlocal frame_debt
            frame_debt += seconds * self.fps
            n = int(frame_debt)
            frame_debt -= n
            return n

        def write_one():
            nonlocal n_written
            self._tick_viewport(self._desired_viewport(cur_line))
            blink_on = ((n_written // blink_period) % 2 == 0
                        if self.cursor_blink else True)
            frame = self.create_frame(cur_line, cur_char,
                                      cursor_visible=self.show_cursor and blink_on)
            writer.write(frame)
            n_written += 1
            if progress_callback and n_written % 15 == 0:
                pct     = n_written / max(n_total, 1) * 100
                elapsed = time.time() - start_time
                eta     = (elapsed / n_written) * (n_total - n_written)
                progress_callback(
                    pct, 100,
                    f"Line {cur_line + 1}/{len(self.wrapped_lines)}", eta)

        def abort():
            writer.release()
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except OSError:
                    pass

        try:
            for line in self.wrapped_lines:
                if cancelled():
                    abort()
                    return False

                # Indentation lands in one go, the way an editor indents.
                indent   = (len(line) - len(line.lstrip())
                            if self.instant_indent else 0)
                cur_char = indent

                for char_idx in range(indent, len(line)):
                    cur_char = char_idx + 1
                    for _ in range(frames_for(self.char_delay)):
                        if cancelled():
                            abort()
                            return False
                        write_one()

                for _ in range(frames_for(self.line_delay)):
                    if cancelled():
                        abort()
                        return False
                    write_one()

                cur_line += 1
                cur_char  = 0

            for _ in range(max(1, frames_for(FINAL_PAUSE_SECONDS))):
                if cancelled():
                    abort()
                    return False
                write_one()

        except Exception as exc:
            print(f"Render error: {exc}")
            if progress_callback:
                progress_callback(0, 100, f"Error: {exc}", 0)
            writer.release()
            return False

        writer.release()

        if os.path.exists(self.output_path):
            size_mb = os.path.getsize(self.output_path) / (1024 * 1024)
            if progress_callback:
                progress_callback(100, 100, f"Done! ({size_mb:.1f} MB)", 0)
            return self.output_path
        return False

    def open_video(self) -> bool:
        if not os.path.exists(self.output_path):
            return False
        try:
            sys_name = platform.system()
            if sys_name == "Windows":
                os.startfile(os.path.abspath(self.output_path))
            elif sys_name == "Darwin":
                subprocess.call(["open", os.path.abspath(self.output_path)])
            else:
                subprocess.call(["xdg-open", os.path.abspath(self.output_path)])
            return True
        except OSError as e:
            print(f"Could not open video: {e}")
            return False


class TypewriterVideoApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Code Typewriter — VS Code themes")
        # Window size is the caller's business (see main(), which fits it to
        # the screen); forcing 1150x800 here pushed the button row past the
        # bottom edge on scaled displays.

        self.code_file             = tk.StringVar()
        self.output_file           = tk.StringVar()
        self.resolution_var        = tk.StringVar(value="1280x720")
        self.fps_var               = tk.IntVar(value=30)
        self.font_size_var         = tk.IntVar(value=18)
        self.animation_type_var    = tk.StringVar(value="smooth")
        self.follow_cursor_var     = tk.BooleanVar(value=True)
        self.smooth_scroll_var     = tk.BooleanVar(value=True)
        self.syntax_highlight_var  = tk.BooleanVar(value=True)
        self.show_line_numbers_var = tk.BooleanVar(value=True)
        self.auto_open_var         = tk.BooleanVar(value=True)
        self.show_cursor_var       = tk.BooleanVar(value=True)
        self.cursor_blink_var      = tk.BooleanVar(value=True)
        self.target_duration_var   = tk.DoubleVar(value=30.0)
        self.use_target_duration   = tk.BooleanVar(value=True)
        self.char_delay_var        = tk.DoubleVar(value=0.05)
        self.line_delay_var        = tk.DoubleVar(value=0.30)
        self.filename_bar_var      = tk.BooleanVar(value=True)
        self.instant_indent_var    = tk.BooleanVar(value=True)
        self.exact_colour_var      = tk.BooleanVar(value=True)

        self.video_info_var     = tk.StringVar(value="No file loaded")
        self.estimated_time_var = tk.StringVar(value="Estimated: --")
        self.target_time_var    = tk.StringVar(value="Target: 30 s")

        self.render_thread        = None
        self.cancel_flag          = threading.Event()
        self.current_code_content = ""
        self._preview_timer       = None
        self._cached_wrap_key     = None
        self._cached_wrapped      = []
        self._render_error        = None
        self._closing             = False
        self._preview_size        = (0, 0)

        # Every theme VS Code has locally, plus the bundled fallback.
        self.themes: List[Tuple[str, str]] = [(DEFAULT_PALETTE.name, "")]
        try:
            self.themes += discover_vscode_themes()
        except Exception as exc:
            print(f"Theme discovery failed: {exc}")
        self._palette_cache: dict = {"": DEFAULT_PALETTE}
        self.palette = DEFAULT_PALETTE
        self.theme_var = tk.StringVar(value=self._default_theme_label())

        self._load_settings()
        self._build_ui()
        self._apply_theme()
        self.code_file.trace_add("write", self._on_code_file_changed)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _default_theme_label(self) -> str:
        """Prefer the theme VS Code is actually set to, else Dark Modern."""
        labels = [name for name, _ in self.themes]
        active = None
        try:
            active = active_vscode_theme_label()
        except Exception:
            pass
        # No explicit setting means VS Code's own default; newer builds ship
        # "Dark 2026", older ones "Dark Modern".
        for wanted in (active, "Dark 2026", "Dark Modern", "Dark+"):
            if not wanted:
                continue
            for name in labels:
                if name == wanted or name.startswith(wanted):
                    return name
        return labels[0]

    def _palette_for(self, label: str) -> "Palette":
        path = dict(self.themes).get(label, "")
        if path in self._palette_cache:
            return self._palette_cache[path]
        try:
            pal = Palette.from_vscode_theme(label, path)
        except Exception as exc:
            print(f"Could not read theme {label!r}: {exc}")
            pal = DEFAULT_PALETTE
        self._palette_cache[path] = pal
        return pal

    def _apply_theme(self, *_):
        self.palette = self._palette_for(self.theme_var.get())
        self._draw_theme_swatches()
        self._schedule_preview()

    @staticmethod
    def _hex(rgb: Tuple[int, int, int]) -> str:
        return "#%02X%02X%02X" % rgb

    def _draw_theme_swatches(self):
        canvas = getattr(self, "theme_swatch", None)
        if canvas is None:
            return
        pal = self.palette
        canvas.delete("all")
        canvas.config(bg=self._hex(pal.bg))

        roles = [
            ("text",     pal.fg),          ("keyword",  pal.keyword_ctrl),
            ("string",   pal.string),      ("number",   pal.number),
            ("function", pal.function),    ("type",     pal.type_),
            ("variable", pal.variable),    ("comment",  pal.comment),
        ]
        cell_w, cell_h, per_row = 106, 20, 4
        for i, (name, rgb) in enumerate(roles):
            colour = self._hex(rgb)
            x = 10 + (i % per_row) * cell_w
            y = 12 + (i // per_row) * cell_h
            canvas.create_rectangle(x, y - 5, x + 11, y + 6,
                                    fill=colour, outline=colour)
            canvas.create_text(x + 17, y, text=name, anchor="w",
                               fill=colour, font=("Consolas", 8))

    def _load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE) as fh:
                s = json.load(fh)
            self.fps_var.set(s.get("fps", 30))
            self.font_size_var.set(s.get("font_size", 18))
            self.target_duration_var.set(s.get("target_duration", 30.0))
            self.char_delay_var.set(s.get("char_delay", 0.05))
            self.line_delay_var.set(s.get("line_delay", 0.30))
            self.resolution_var.set(s.get("resolution", "1280x720"))
            self.filename_bar_var.set(s.get("filename_bar", True))
            self.instant_indent_var.set(s.get("instant_indent", True))
            self.exact_colour_var.set(s.get("exact_colour", False))
            saved_theme = s.get("theme")
            if saved_theme and saved_theme in [n for n, _ in self.themes]:
                self.theme_var.set(saved_theme)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError,
                tk.TclError):
            pass

    def _save_settings(self):
        try:
            s = {
                "fps":             self.fps_var.get(),
                "font_size":       self.font_size_var.get(),
                "target_duration": self.target_duration_var.get(),
                "char_delay":      self.char_delay_var.get(),
                "line_delay":      self.line_delay_var.get(),
                "resolution":      self.resolution_var.get(),
                "theme":           self.theme_var.get(),
                "filename_bar":    self.filename_bar_var.get(),
                "instant_indent":  self.instant_indent_var.get(),
                "exact_colour":    self.exact_colour_var.get(),
            }
            with open(SETTINGS_FILE, "w") as fh:
                json.dump(s, fh, indent=2)
        except OSError:
            pass

    def _build_ui(self):
        # The progress bar goes straight into the root, anchored at the bottom:
        # packed inside `main` it would land in the sliver left over by the
        # LEFT/RIGHT panes and end up with (almost) no width.
        bottom = ttk.Frame(self.root)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 10))

        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left = ttk.Frame(main, width=460)
        left.pack(side=tk.LEFT, fill=tk.BOTH, padx=(0, 10))
        left.pack_propagate(False)

        right = ttk.Frame(main)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        nb = ttk.Notebook(left)
        nb.pack(fill=tk.BOTH, expand=True)

        for label, builder in [
            ("File",      self._tab_file),
            ("Theme",     self._tab_theme),
            ("Video",     self._tab_video),
            ("Animation", self._tab_animation),
            ("Duration",  self._tab_duration),
        ]:
            frame = ttk.Frame(nb)
            nb.add(frame, text=label)
            builder(frame)

        self._build_preview(right)
        self._build_info_bar(right)
        self._build_progress_bar(bottom)

    def _tab_file(self, p):
        ttk.Label(p, text="Code file", font=("Arial", 10, "bold")).pack(
            anchor=tk.W, pady=(10, 4))
        f = ttk.Frame(p); f.pack(fill=tk.X, padx=5, pady=4)
        ttk.Entry(f, textvariable=self.code_file).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(f, text="Browse…", command=self._browse_code).pack(side=tk.RIGHT)

        ttk.Label(p, text="Output video", font=("Arial", 10, "bold")).pack(
            anchor=tk.W, pady=(10, 4))
        f2 = ttk.Frame(p); f2.pack(fill=tk.X, padx=5, pady=4)
        ttk.Entry(f2, textvariable=self.output_file).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(f2, text="Browse…", command=self._browse_output).pack(side=tk.RIGHT)

        ttk.Checkbutton(p, text="Open video automatically when done",
                        variable=self.auto_open_var).pack(anchor=tk.W, pady=8)

    def _tab_theme(self, p):
        ttk.Label(p, text="Colour theme", font=("Arial", 10, "bold")).pack(
            anchor=tk.W, pady=(10, 4))
        ttk.Label(
            p, text=f"{len(self.themes)} themes found "
                    "(installed VS Code themes, plus a bundled fallback)",
            font=("Arial", 8)).pack(anchor=tk.W, padx=5)

        list_frame = ttk.Frame(p)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(6, 4))

        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.theme_list = tk.Listbox(
            list_frame, height=12, activestyle="dotbox", selectmode=tk.BROWSE,
            exportselection=False, yscrollcommand=scroll.set)
        self.theme_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.config(command=self.theme_list.yview)

        for name, _path in self.themes:
            self.theme_list.insert(tk.END, name)

        current = self.theme_var.get()
        names   = [n for n, _ in self.themes]
        if current in names:
            idx = names.index(current)
            self.theme_list.selection_set(idx)
            self.theme_list.see(idx)

        self.theme_list.bind("<<ListboxSelect>>", self._on_theme_selected)

        # Swatches drawn in the theme's own colours, on its own background.
        self.theme_swatch = tk.Canvas(p, height=64, highlightthickness=1,
                                      highlightbackground="#808080")
        self.theme_swatch.pack(fill=tk.X, padx=5, pady=(6, 0))
        ttk.Label(p, text="Selecting a theme updates the preview immediately.",
                  font=("Arial", 8)).pack(anchor=tk.W, padx=5, pady=(6, 8))

    def _on_theme_selected(self, *_):
        sel = self.theme_list.curselection()
        if not sel:
            return
        self.theme_var.set(self.themes[sel[-1]][0])
        self._apply_theme()

    def _tab_video(self, p):
        def labeled_scale(parent, label, var, lo, hi):
            ttk.Label(parent, text=label,
                      font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(10, 4))
            f = ttk.Frame(parent); f.pack(fill=tk.X, padx=5)
            lbl = ttk.Label(f, width=5); lbl.pack(side=tk.RIGHT)
            def on_change(v):
                lbl.config(text=str(int(float(v))))
                self._schedule_preview()
                self._update_info()
            s = tk.Scale(f, from_=lo, to=hi, variable=var,
                         orient=tk.HORIZONTAL, showvalue=0, command=on_change)
            s.pack(fill=tk.X, expand=True)
            lbl.config(text=str(var.get()))

        ttk.Label(p, text="Resolution", font=("Arial", 10, "bold")).pack(
            anchor=tk.W, pady=(10, 4))
        combo = ttk.Combobox(
            p, textvariable=self.resolution_var,
            values=["640x480", "1280x720", "1920x1080", "2560x1440"],
            state="readonly",
        )
        combo.pack(fill=tk.X, padx=5)
        combo.bind("<<ComboboxSelected>>", self._on_resolution_change)

        labeled_scale(p, "Frames per second", self.fps_var,      15, 60)
        labeled_scale(p, "Font size (px)",    self.font_size_var, 12, 36)

        ttk.Separator(p, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=12)
        ttk.Checkbutton(p, text="Syntax highlighting (VS Code Dark+)",
                        variable=self.syntax_highlight_var,
                        command=self._schedule_preview).pack(anchor=tk.W)
        ttk.Checkbutton(p, text="Show line numbers",
                        variable=self.show_line_numbers_var,
                        command=self._on_gutter_change).pack(anchor=tk.W, pady=4)
        ttk.Checkbutton(p, text="Show file name tab at the top",
                        variable=self.filename_bar_var,
                        command=self._schedule_preview).pack(anchor=tk.W)

        ttk.Separator(p, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=12)
        ttk.Label(p, text="Colour accuracy", font=("Arial", 10, "bold")).pack(
            anchor=tk.W, pady=(0, 4))
        ttk.Checkbutton(p, text="Exact theme colours (H.264 4:4:4)",
                        variable=self.exact_colour_var).pack(anchor=tk.W)
        ttk.Label(p, justify=tk.LEFT, font=("Arial", 8),
                  text="On (default): exact theme colours, sharp coloured\n"
                       "text edges. Plays in VLC, mpv and browsers, but\n"
                       "Windows Media Player cannot open 4:4:4.\n"
                       "Off: full-range 4:2:0 — plays in Media Player too,\n"
                       "costs about one level on dark tones.").pack(
            anchor=tk.W, padx=5, pady=(2, 0))

    def _on_resolution_change(self, *_):
        self._schedule_preview()
        self._update_info()

    def _on_gutter_change(self, *_):
        # Gutter width changes the wrap width, so the estimate moves too.
        self._schedule_preview()
        self._update_info()

    def _tab_animation(self, p):
        ttk.Label(p, text="Animation style",
                  font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(10, 4))
        for val, label in [
            ("smooth",     "Smooth  (default)"),
            ("fast",       "Fast"),
            ("typewriter", "Typewriter  (underline cursor)"),
        ]:
            ttk.Radiobutton(p, text=label, variable=self.animation_type_var,
                            value=val, command=self._update_info).pack(anchor=tk.W)

        def delay_row(parent, label, var):
            ttk.Label(parent, text=label,
                      font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(10, 4))
            f = ttk.Frame(parent); f.pack(fill=tk.X, padx=5)
            lbl = ttk.Label(f, text=f"{var.get():.2f}s", width=6)
            lbl.pack(side=tk.RIGHT)
            def on_change(v):
                val = float(v) / 100
                lbl.config(text=f"{val:.2f}s")
                var.set(val)
                self._update_info()
            s = tk.Scale(f, from_=1, to=200, orient=tk.HORIZONTAL,
                         showvalue=0, command=on_change)
            s.set(int(var.get() * 100))
            s.pack(fill=tk.X, expand=True)

        delay_row(p, "Character delay (seconds)", self.char_delay_var)
        delay_row(p, "Line delay (seconds)",      self.line_delay_var)

        ttk.Separator(p, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=10)
        ttk.Checkbutton(p, text="Follow cursor while typing",
                        variable=self.follow_cursor_var).pack(anchor=tk.W)
        ttk.Checkbutton(p, text="Smooth scrolling",
                        variable=self.smooth_scroll_var).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(p, text="Show cursor",
                        variable=self.show_cursor_var).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(p, text="Blinking cursor",
                        variable=self.cursor_blink_var).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(p, text="Indent instantly, do not type leading spaces",
                        variable=self.instant_indent_var,
                        command=self._update_info).pack(anchor=tk.W, pady=2)

    def _tab_duration(self, p):
        ttk.Checkbutton(p, text="Use fixed target duration",
                        variable=self.use_target_duration,
                        command=self._update_info).pack(anchor=tk.W, pady=(10, 4))

        ttk.Label(p, text="Target duration (seconds)",
                  font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(10, 4))
        f = ttk.Frame(p); f.pack(fill=tk.X, padx=5)
        self._dur_lbl = ttk.Label(f, text=f"{self.target_duration_var.get():.0f}s",
                                  width=6)
        self._dur_lbl.pack(side=tk.RIGHT)
        def on_dur(v):
            self._dur_lbl.config(text=f"{float(v):.0f}s")
            self.target_duration_var.set(float(v))
            self.target_time_var.set(f"Target: {float(v):.0f} s")
            self._update_info()
        tk.Scale(f, from_=5, to=300, variable=self.target_duration_var,
                 orient=tk.HORIZONTAL, showvalue=0, command=on_dur).pack(
            fill=tk.X, expand=True)

        ttk.Label(p, text="Quick presets",
                  font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(12, 4))
        pf = ttk.Frame(p); pf.pack(fill=tk.X, padx=5)
        for label, secs in [("15 s", 15), ("30 s", 30),
                             ("60 s", 60), ("90 s", 90), ("2 min", 120)]:
            ttk.Button(pf, text=label, width=7,
                       command=lambda v=secs: self._set_duration(v)).pack(
                side=tk.LEFT, padx=2)

        ttk.Separator(p, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=12)
        ttk.Label(p, textvariable=self.estimated_time_var,
                  font=("Arial", 9, "bold")).pack(anchor=tk.W)
        ttk.Label(p, textvariable=self.target_time_var,
                  font=("Arial", 9)).pack(anchor=tk.W, pady=(4, 0))

    def _set_duration(self, secs: int):
        self.target_duration_var.set(secs)
        self._dur_lbl.config(text=f"{secs}s")
        self.target_time_var.set(f"Target: {secs} s")
        self._update_info()

    def _build_preview(self, parent):
        pf = ttk.LabelFrame(parent, text="Preview", padding=6)
        pf.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas = tk.Canvas(pf, bg="#1e1e1e", highlightthickness=0)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        # Redraw on resize so the preview keeps filling the panel.
        self.preview_canvas.bind("<Configure>", self._on_preview_resize)
        # Tracked so _on_close can cancel it; an uncancelled callback fires
        # after the window is gone and raises "invalid command name".
        self._preview_timer = self.root.after(100, self._update_preview)

    def _on_preview_resize(self, event):
        # Ignore the small jitter that comes with every layout pass.
        if (abs(event.width  - self._preview_size[0]) > 4 or
                abs(event.height - self._preview_size[1]) > 4):
            self._preview_size = (event.width, event.height)
            self._schedule_preview()

    def _schedule_preview(self, *_):
        if self._preview_timer:
            self.root.after_cancel(self._preview_timer)
        self._preview_timer = self.root.after(350, self._update_preview)

    def _update_preview(self):
        try:
            self._render_preview()
        except Exception:
            pass

    def _render_preview(self):
        self.preview_canvas.delete("all")
        try:
            w, h = map(int, self.resolution_var.get().split("x"))
        except ValueError:
            w, h = 1280, 720
        # Fill the panel: scale the frame to whatever space the canvas has,
        # keeping the video's aspect ratio.
        avail_w = self.preview_canvas.winfo_width()
        avail_h = self.preview_canvas.winfo_height()
        if avail_w < 20 or avail_h < 20:        # not laid out yet
            avail_w, avail_h = PREVIEW_MAX_W, PREVIEW_MAX_H

        scale = min(avail_w / w, avail_h / h)
        pw = max(100, int(w * scale))
        ph = max(60,  int(h * scale))

        pal  = self.palette
        img  = Image.new("RGB", (pw, ph), pal.bg)
        draw = ImageDraw.Draw(img)

        fs   = max(7, int(self.font_size_var.get() * scale))
        font = self._load_preview_font(fs)

        cw = max(1.0, _tw(font, "M"))
        lh = max(8, int(_th(font, "Mg") * 1.35))

        show_gutter = self.show_line_numbers_var.get()
        gutter_w    = int(cw * 4) if show_gutter else 0
        sep_x       = 4 + gutter_w + 2 if show_gutter else None
        text_x0     = 4 + gutter_w + (5 if show_gutter else 0)

        bar_h = 0
        if self.filename_bar_var.get():
            bar_h = max(10, int(lh * 1.9))
            name  = os.path.basename(self.code_file.get()) or "untitled"
            tab_w = int(_tw(font, name) + max(8, cw * 3))
            draw.rectangle([0, 0, pw, bar_h], fill=pal.tab_bg)
            draw.rectangle([0, 0, tab_w, bar_h], fill=pal.tab_active_bg)
            accent = max(1, int(bar_h * 0.07))
            draw.rectangle([0, 0, tab_w, accent], fill=pal.tab_accent)
            draw.line([(tab_w, 0), (tab_w, bar_h - 1)], fill=pal.tab_border)
            draw.line([(0, bar_h - 1), (pw, bar_h - 1)], fill=pal.tab_border)
            draw.text((max(4, cw * 1.5), accent + max(0, (bar_h - accent - lh) // 2)),
                      name, font=font, fill=pal.tab_active_fg)

        top = bar_h + 4
        if sep_x is not None:
            draw.line([(sep_x, top), (sep_x, ph - 4)],
                      fill=pal.gutter_border, width=1)

        source = self.current_code_content
        tok_per_line: List[List[Tuple]] = []

        if PYGMENTS_OK and self.syntax_highlight_var.get() and source.strip():
            try:
                fpath = self.code_file.get()
                if fpath and os.path.exists(fpath):
                    lexer = get_lexer_for_filename(fpath)
                else:
                    from pygments.lexers import PythonLexer
                    lexer = PythonLexer()
                raw = list(lex(source, lexer))
                flat: List[Tuple] = [(_pygments_color(t, pal), s) for t, s in raw]
                per_line: List[List[Tuple]] = [[]]
                for color, ttext in flat:
                    parts = ttext.split("\n")
                    for k, part in enumerate(parts):
                        if k > 0:
                            per_line.append([])
                        if part:
                            per_line[-1].append((color, part))
                tok_per_line = per_line
            except Exception:
                tok_per_line = []

        lines    = source.split("\n")
        max_rows = max(1, int((ph - top - 4) // lh))
        y = top
        for lineno, line in enumerate(lines[:max_rows], 1):
            if y + lh > ph:
                break
            if show_gutter:
                ns  = str(lineno)
                nw  = _tw(font, ns)
                draw.text((4 + gutter_w - nw - 2, y), ns,
                          font=font, fill=pal.gutter_fg)


            x     = text_x0
            max_x = pw - 4

            if tok_per_line and (lineno - 1) < len(tok_per_line):
                for color, ttext in tok_per_line[lineno - 1]:
                    if not self.syntax_highlight_var.get():
                        color = pal.fg
                    if x >= max_x:
                        break
                    tw = _tw(font, ttext)
                    if x + tw > max_x:
                        chars_fit = max(0, int((max_x - x) // cw))
                        ttext = ttext[:chars_fit]
                    if ttext:
                        draw.text((x, y), ttext, font=font, fill=color)
                    x += _tw(font, ttext)
            else:
                chars_fit = max(0, int((max_x - x) // cw))
                draw.text((x, y), line[:chars_fit], font=font, fill=pal.fg)

            y += lh

        if y + lh - 2 <= ph:
            draw.rectangle([text_x0, y, text_x0 + 2, y + lh - 2],
                           fill=pal.cursor)

        self._preview_photo = ImageTk.PhotoImage(img)
        # Centre it, and match the letterbox to the theme so the panel reads
        # as one surface instead of a small frame in a black box.
        self.preview_canvas.config(bg="#%02X%02X%02X" % pal.bg)
        self.preview_canvas.create_image(max(0, (avail_w - pw) // 2),
                                         max(0, (avail_h - ph) // 2),
                                         anchor=tk.NW,
                                         image=self._preview_photo)

    @staticmethod
    def _load_preview_font(size: int) -> ImageFont.FreeTypeFont:
        for path in [
            r"C:\Windows\Fonts\CascadiaCode.ttf",
            r"C:\Windows\Fonts\consola.ttf",
            r"C:\Windows\Fonts\cour.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ]:
            if os.path.exists(path):
                try:
                    kwargs = {"index": 0} if path.lower().endswith(".ttc") else {}
                    return ImageFont.truetype(path, size, **kwargs)
                except (OSError, IOError):
                    pass
        return ImageFont.load_default()

    def _build_info_bar(self, parent):
        f = ttk.LabelFrame(parent, text="Video info", padding=8)
        f.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(f, textvariable=self.video_info_var,
                  font=("Arial", 9)).pack(anchor=tk.W)
        ttk.Label(f, textvariable=self.estimated_time_var,
                  font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(f, textvariable=self.target_time_var,
                  font=("Arial", 9)).pack(anchor=tk.W)
        ttk.Button(f, text="Refresh info", command=self._update_info).pack(pady=(8, 0))

    def _build_progress_bar(self, parent):
        f = ttk.Frame(parent); f.pack(fill=tk.X, pady=(10, 0))
        self.progress_var = tk.DoubleVar()
        ttk.Progressbar(f, variable=self.progress_var,
                        maximum=100).pack(fill=tk.X, pady=(0, 4))
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(f, textvariable=self.status_var).pack(anchor=tk.W)
        bf = ttk.Frame(f); bf.pack(fill=tk.X, pady=(8, 0))
        self.start_btn = ttk.Button(bf, text="▶  Start rendering",
                                    command=self._start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.cancel_btn = ttk.Button(bf, text="✕  Cancel",
                                     command=self._cancel, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT)

    def _safe_message(self, kind: str, title: str, message: str):
        try:
            if self._closing or not self.root.winfo_exists():
                return
            if kind == "error":
                messagebox.showerror(title, message)
            else:
                messagebox.showinfo(title, message)
        except tk.TclError:
            pass

    def _post(self, fn, *args):
        """Hop back to the Tk thread, tolerating a window that is already gone."""
        if self._closing:
            return
        try:
            self.root.after(0, fn, *args)
        except (RuntimeError, tk.TclError):
            pass

    def _get_wrapped_lines(self) -> List[str]:
        fpath = self.code_file.get()
        key   = (fpath, self.font_size_var.get(), self.resolution_var.get(),
                 self.show_line_numbers_var.get())
        if key == self._cached_wrap_key:
            return self._cached_wrapped

        if not fpath or not os.path.exists(fpath):
            return []

        try:
            w, _ = map(int, self.resolution_var.get().split("x"))
        except ValueError:
            w = 1280
        padding   = 20
        font      = self._load_preview_font(self.font_size_var.get())
        cw        = max(1.0, _tw(font, "M"))
        gutter_w  = cw * 6 + 7 if self.show_line_numbers_var.get() else 0
        usable_w  = w - 2 * padding - gutter_w
        cpp       = max(10, int(usable_w // cw))

        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            wrapped = []
            for line in lines:
                w_list = textwrap.wrap(
                    line.rstrip("\n").expandtabs(TAB_WIDTH), width=cpp,
                    replace_whitespace=False, expand_tabs=False) or [""]
                wrapped.extend(w_list)
            self._cached_wrapped  = wrapped
            self._cached_wrap_key = key
            return wrapped
        except OSError:
            return []

    def _update_info(self, *_):
        fpath = self.code_file.get()
        if not fpath or not os.path.exists(fpath):
            self.video_info_var.set("No file loaded")
            self.estimated_time_var.set("Estimated: --")
            return

        wrapped     = self._get_wrapped_lines()
        # Indentation isn't typed, so it doesn't count toward the duration.
        total_chars = sum(len(l.lstrip()) if self.instant_indent_var.get()
                          else len(l) for l in wrapped)
        total_lines = len(wrapped)

        cd = self.char_delay_var.get()
        ld = self.line_delay_var.get()

        if self.use_target_duration.get() and self.target_duration_var.get() > 0:
            target = self.target_duration_var.get()
            avail  = target - FINAL_PAUSE_SECONDS
            if avail > 0 and total_chars + total_lines > 0:
                k  = ld / max(cd, 1e-9)
                d  = avail / (total_chars + total_lines * k)
                cd = max(0.0002, min(0.5, d))
                ld = max(0.0010, min(2.0, d * k))

        est = total_chars * cd + total_lines * ld + FINAL_PAUSE_SECONDS
        self.video_info_var.set(
            f"Lines: {total_lines:,}  |  Chars: {total_chars:,}"
            f"  |  FPS: {self.fps_var.get()}")
        self.estimated_time_var.set(f"Estimated: {est:.1f} s")
        self.target_time_var.set(
            f"Target: {self.target_duration_var.get():.0f} s")

    def _browse_code(self):
        fname = filedialog.askopenfilename(
            title="Select source file",
            filetypes=[
                ("Python",   "*.py"),
                ("JavaScript","*.js *.ts *.jsx *.tsx"),
                ("Java",     "*.java"),
                ("C / C++",  "*.c *.cpp *.h *.hpp"),
                ("Text",     "*.txt"),
                ("All",      "*.*"),
            ])
        if not fname:
            return
        self.code_file.set(fname)      # _on_code_file_changed does the loading
        if not self.output_file.get():
            base    = os.path.splitext(os.path.basename(fname))[0]
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            out_dir = desktop if os.path.exists(desktop) else "."
            self.output_file.set(os.path.join(out_dir, f"{base}_typewriter.mp4"))

    def _on_code_file_changed(self, *_):
        """Reload preview/estimate whenever the path changes.

        The entry is editable, so a typed or pasted path has to work exactly
        like picking one through Browse….
        """
        fname = self.code_file.get().strip()
        self._cached_wrap_key = None
        self._cached_wrapped  = []
        if not fname or not os.path.isfile(fname):
            self.current_code_content = ""
        else:
            try:
                with open(fname, "r", encoding="utf-8", errors="replace") as fh:
                    self.current_code_content = (
                        fh.read().expandtabs(TAB_WIDTH)[:PREVIEW_CHAR_LIMIT])
            except OSError:
                self.current_code_content = ""
        self._schedule_preview()
        self._update_info()

    def _browse_output(self):
        fname = filedialog.asksaveasfilename(
            title="Save video as",
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4"), ("All files", "*.*")])
        if not fname:
            return
        if not os.path.splitext(fname)[1]:
            fname += ".mp4"
        out_dir = os.path.dirname(os.path.abspath(fname))
        if out_dir and not os.path.exists(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as e:
                self._safe_message("error", "Output error", str(e))
                return
        self.output_file.set(fname)

    def _validate_inputs(self) -> Optional[str]:
        fpath = self.code_file.get().strip()
        if not fpath or not os.path.exists(fpath):
            return "Please select a valid code file."

        out_path = self.output_file.get().strip()
        if not out_path:
            return "Please specify an output path."

        out_dir = os.path.dirname(os.path.abspath(out_path))
        if out_dir and not os.path.exists(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as exc:
                return f"Could not create output folder:\n{exc}"

        try:
            w, h = map(int, self.resolution_var.get().split("x"))
        except ValueError:
            return "Use WxH format, e.g. 1280x720"

        if w <= 0 or h <= 0:
            return "Resolution must be positive."

        return None

    def _start(self):
        if self.render_thread is not None and self.render_thread.is_alive():
            return

        error = self._validate_inputs()
        if error:
            self._safe_message("error", "Input error", error)
            return

        try:
            w, h = map(int, self.resolution_var.get().split("x"))
        except ValueError:
            w, h = 1280, 720

        # Snapshot every setting here, on the Tk thread: tkinter variables must
        # not be read from the worker thread.
        cfg = dict(
            code_path           = self.code_file.get(),
            output_path         = self.output_file.get(),
            resolution          = (w, h),
            fps                 = self.fps_var.get(),
            font_size           = self.font_size_var.get(),
            char_delay          = self.char_delay_var.get(),
            line_delay          = self.line_delay_var.get(),
            show_cursor         = self.show_cursor_var.get(),
            cursor_blink        = self.cursor_blink_var.get(),
            follow_cursor       = self.follow_cursor_var.get(),
            smooth_scroll       = self.smooth_scroll_var.get(),
            syntax_highlighting = self.syntax_highlight_var.get(),
            show_line_numbers   = self.show_line_numbers_var.get(),
            animation_type      = self.animation_type_var.get(),
            target_duration     = (self.target_duration_var.get()
                                   if self.use_target_duration.get() else 0.0),
            palette             = self.palette,
            show_filename_bar   = self.filename_bar_var.get(),
            instant_indent      = self.instant_indent_var.get(),
            exact_colour        = self.exact_colour_var.get(),
        )

        self._save_settings()
        self._render_error = None
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.status_var.set("Starting render…")
        self.cancel_flag.clear()
        self.render_thread = threading.Thread(
            target=self._render_worker, args=(cfg,), daemon=True)
        self.render_thread.start()

    def _render_worker(self, cfg: dict):
        result = False
        try:
            renderer = CodeTypewriterVideoRenderer(**cfg)
            result = renderer.render(
                progress_callback = self._on_progress,
                cancel_flag       = self.cancel_flag,
            )
        except Exception as exc:
            self._render_error = str(exc)
            print(f"Render error: {exc}")

        self._post(self._on_done, result)

    def _on_progress(self, percent, _total, status, eta):
        self._post(self._apply_progress, percent, status, eta)

    def _apply_progress(self, percent, status, eta):
        if self._closing:
            return
        self.progress_var.set(percent)
        self.status_var.set(
            f"{status}   ETA: {eta:.0f} s" if eta > 0 else status)

    def _cancel(self):
        self.cancel_flag.set()
        self.status_var.set("Cancelling…")

    def _on_close(self):
        if self.render_thread is not None and self.render_thread.is_alive():
            if not messagebox.askyesno(
                    "Rendering in progress",
                    "A render is still running. Cancel it and quit?"):
                return
            self.cancel_flag.set()
            self.render_thread.join(timeout=5.0)
        self._closing = True
        if self._preview_timer:
            try:
                self.root.after_cancel(self._preview_timer)
            except tk.TclError:
                pass
        self.root.destroy()

    def _open_output_file(self, result: str):
        try:
            sys_name = platform.system()
            if sys_name == "Windows":
                os.startfile(os.path.abspath(result))
            elif sys_name == "Darwin":
                subprocess.call(["open", os.path.abspath(result)])
            else:
                subprocess.call(["xdg-open", os.path.abspath(result)])
        except OSError as e:
            self._safe_message("info", "Saved", f"Saved to:\n{result}\n\n{e}")

    def _on_done(self, result):
        if self._closing:
            return
        self.start_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)

        if isinstance(result, str) and os.path.exists(result):
            self.progress_var.set(100)
            self.status_var.set("Done!")
            if self.auto_open_var.get():
                if messagebox.askyesno(
                        "Render complete",
                        f"Video saved:\n{os.path.abspath(result)}\n\nOpen now?"):
                    self._open_output_file(result)
            else:
                self._safe_message(
                    "info",
                    "Render complete",
                    f"Saved:\n{os.path.abspath(result)}",
                )

        elif not self.cancel_flag.is_set():
            self.status_var.set("Rendering failed.")
            detail = self._render_error or ("Could not create video. "
                                            "Check the console for details.")
            self._safe_message("error", "Error", detail)
        else:
            self.status_var.set("Cancelled.")
            self.progress_var.set(0)


def _enable_dpi_awareness():
    """Tell Windows we handle scaling ourselves.

    Without this Windows scales the whole window bitmap on a 125 % display,
    so a requested 1150x800 becomes about 1458x1000 real pixels, blurry and
    too tall for a 1080p desktop.
    """
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # system DPI aware
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main():
    _enable_dpi_awareness()

    root = tk.Tk()
    root.update_idletasks()

    # Keep widget/font sizes sane now that we get real pixels.
    try:
        dpi = root.winfo_fpixels("1i")
        if dpi > 0:
            root.tk.call("tk", "scaling", dpi / 72.0)
    except tk.TclError:
        pass

    # Fit the screen instead of assuming 1150x800 fits: the Start/Cancel row
    # used to fall off the bottom edge on smaller or scaled displays.
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    w = max(820, min(1250, screen_w - 80))
    h = max(560, min(880,  screen_h - 140))
    x = max(0, (screen_w - w) // 2)
    y = max(0, (screen_h - h) // 4)

    root.geometry(f"{w}x{h}+{x}+{y}")
    root.minsize(820, 560)
    TypewriterVideoApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

