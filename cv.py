"""
Code Typewriter Video Creator
──────────────────────────────
Renders a "live coding" video of any source file with authentic
VS Code Dark+ syntax highlighting.

Dependencies:
    pip install opencv-python pillow pygments

Optional (better fonts on Windows):
    Install Cascadia Code from https://github.com/microsoft/cascadia-code
"""

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk
import textwrap
import os
import sys
import platform
import subprocess
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
from datetime import datetime
from typing import List, Tuple, Optional
import json

# ── Pygments ────────────────────────────────────────────────────────
try:
    from pygments import lex
    from pygments.lexers import get_lexer_for_filename, guess_lexer, TextLexer
    from pygments.token import Token
    PYGMENTS_OK = True
except ImportError:
    PYGMENTS_OK = False
    print("WARNING: pygments not installed.  pip install pygments")


# ══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════
PREVIEW_CHAR_LIMIT  = 3000
PREVIEW_MAX_LINES   = 18
PREVIEW_SCALE       = 0.30
FINAL_PAUSE_SECONDS = 2.0
SETTINGS_FILE       = "typewriter_settings.json"


# ══════════════════════════════════════════════════════════════════════
#  VS CODE DARK+ COLOUR PALETTE  (exact hex values from the theme JSON)
# ══════════════════════════════════════════════════════════════════════
def _h(hex_str: str) -> Tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

# Token colours
C_PLAIN       = _h("#D4D4D4")   # editor.foreground
C_COMMENT     = _h("#6A9955")   # comment → green
C_STRING      = _h("#CE9178")   # string literal → orange-brown
C_NUMBER      = _h("#B5CEA8")   # numeric constant → light green
C_PURPLE      = _h("#C586C0")   # keyword.control → purple
C_BLUE        = _h("#569CD6")   # storage.type / keyword / constant.language → blue
C_YELLOW      = _h("#DCDCAA")   # entity.name.function / support.function → yellow
C_TEAL        = _h("#4EC9B0")   # entity.name.class / type → teal
C_LIGHT_BLUE  = _h("#9CDCFE")   # variable → light blue
C_BRIGHT_BLUE = _h("#4FC1FF")   # variable.other.constant → bright blue

# UI colours
VSCODE_BG            = _h("#1E1E1E")
VSCODE_GUTTER_FG     = _h("#858585")   # inactive line numbers
VSCODE_GUTTER_ACTIVE = _h("#C6C6C6")   # active line number
VSCODE_GUTTER_BORDER = _h("#404040")   # gutter separator
VSCODE_CURSOR        = _h("#AEAFAD")   # cursor bar

# Keyword classification sets
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

# Pygments token → colour fallback table (most-specific first)
PYGMENTS_COLOR_MAP: List[Tuple] = [
    (Token.Comment,               C_COMMENT),
    (Token.Literal.String.Doc,    C_COMMENT),
    (Token.Literal.String,        C_STRING),
    (Token.Literal.Number,        C_NUMBER),
    (Token.Keyword.Constant,      C_BLUE),
    (Token.Keyword.Namespace,     C_BLUE),
    (Token.Keyword.Type,          C_BLUE),
    (Token.Keyword,               C_PURPLE),
    (Token.Operator.Word,         C_BLUE),
    (Token.Operator,              C_PLAIN),
    (Token.Punctuation,           C_PLAIN),
    (Token.Name.Builtin.Pseudo,   C_BLUE),
    (Token.Name.Builtin,          C_YELLOW),
    (Token.Name.Class,            C_TEAL),
    (Token.Name.Exception,        C_TEAL),
    (Token.Name.Namespace,        C_TEAL),
    (Token.Name.Function.Magic,   C_YELLOW),
    (Token.Name.Function,         C_YELLOW),
    (Token.Name.Decorator,        C_YELLOW),
    (Token.Name.Attribute,        C_LIGHT_BLUE),
    (Token.Name.Variable.Magic,   C_LIGHT_BLUE),
    (Token.Name.Variable,         C_LIGHT_BLUE),
    (Token.Name.Constant,         C_BRIGHT_BLUE),
    (Token.Name,                  C_LIGHT_BLUE),
    (Token,                       C_PLAIN),
]

def _pygments_color(ttype) -> Tuple[int, int, int]:
    for token_cls, color in PYGMENTS_COLOR_MAP:
        if ttype is token_cls or ttype in token_cls:
            return color
    return C_PLAIN


# ══════════════════════════════════════════════════════════════════════
#  TINY FONT HELPERS
# ══════════════════════════════════════════════════════════════════════
def _tw(font, text: str) -> int:
    """Pixel width of *text* with *font*."""
    if not text:
        return 0
    bb = font.getbbox(text)
    return bb[2] - bb[0]

def _th(font, text: str = "Mg") -> int:
    """Pixel height of *text* with *font*."""
    bb = font.getbbox(text)
    return bb[3] - bb[1]


# ══════════════════════════════════════════════════════════════════════
#  RENDERER
# ══════════════════════════════════════════════════════════════════════
class CodeTypewriterVideoRenderer:
    """
    Renders a source file as a typewriter-style coding video.

    Fixed bugs vs original:
      B1  settings loaded after tk.Vars → CRITICAL crash on startup
      B2  frames_per_line could be 0 → silent zero-frame lines
      B3  _slice_tokens search_start advanced by 1 instead of len(wrapped)
          → wrong token slicing when the same substring repeats on one line
      B4  calculate_total_frames off-by-one (range len+1 in render loop)
      B5  _get_wrapped_lines used hardcoded width=80 (GUI only)
      B6  smooth-scroll could overshoot on large jumps
      B7  VideoWriter codec: tries avc1 first, falls back to mp4v
      B8  .ttc font on macOS needs index=0
      B9  render() cursor tracking: cursor_line/cursor_char were redundant
          duplicates of current_line/current_char; merged into one pair
    """

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
    ):
        self.code_path           = code_path
        self.syntax_highlighting = syntax_highlighting
        self.show_line_numbers   = show_line_numbers
        self.follow_cursor       = follow_cursor
        self.smooth_scroll       = smooth_scroll
        self.animation_type      = animation_type
        self.target_duration     = target_duration
        self.show_cursor         = show_cursor
        self.cursor_blink        = cursor_blink

        # Output path
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

        # FIX B-: animation presets set char/line delay
        delay_presets = {
            "fast":       (0.02, 0.10),
            "typewriter": (0.10, 0.50),
            "smooth":     (char_delay, line_delay),
        }
        self.char_delay, self.line_delay = delay_presets.get(
            animation_type, (char_delay, line_delay))

        # Colours — VS Code Dark+ only
        self.background_color = VSCODE_BG
        self.text_color       = C_PLAIN
        self.cursor_color     = VSCODE_CURSOR
        self._gutter_fg       = VSCODE_GUTTER_FG
        self._gutter_active   = VSCODE_GUTTER_ACTIVE
        self._gutter_border   = VSCODE_GUTTER_BORDER

        # Font
        self.font = self._load_font(font_path, font_size)

        # Measure character cell from capital M + descenders
        _cap_bb   = self.font.getbbox("M")
        _full_bb  = self.font.getbbox("Mg")
        self.char_width   = _cap_bb[2]  - _cap_bb[0]
        self.char_height  = _cap_bb[3]  - _cap_bb[1]
        self.line_height_px = int((_full_bb[3] - _full_bb[1]) * 1.35)

        # Viewport
        self.viewport_offset = 0.0
        screen_h = resolution[1] - 2 * self.padding
        self.max_visible_lines = screen_h // self.line_height_px

        # Will be populated by load_code()
        self.wrapped_lines:   List[str]         = []
        self.tokenized_lines: List[List[Tuple]] = []
        self.line_numbers:    List[Optional[int]]= []
        self._gutter_width    = 0
        self._text_x_start    = self.padding

        # Persistent PIL surface (avoids re-allocating every frame)
        self._frame_img  = Image.new("RGB", self.resolution, self.background_color)
        self._frame_draw = ImageDraw.Draw(self._frame_img)

        self.load_code()

        if self.target_duration > 0:
            self._set_exact_duration(self.target_duration)

    # ──────────────────────────────────────────────────────────────
    #  FONT LOADING   (B8 fixed: .ttc uses index=0)
    # ──────────────────────────────────────────────────────────────
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
                "/System/Library/Fonts/Menlo.ttc",   # needs index below
                "/System/Library/Fonts/SFMono-Regular.otf",
            ]
        else:   # Linux / other
            candidates += [
                "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf",
            ]

        for path in candidates:
            if not path or not os.path.exists(path):
                continue
            try:
                # B8: .ttc collections need explicit index
                kwargs = {"index": 0} if path.lower().endswith(".ttc") else {}
                return ImageFont.truetype(path, size, **kwargs)
            except (IOError, OSError):
                continue

        # Last-resort: scan system font dir (Windows)
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

    # ──────────────────────────────────────────────────────────────
    #  CODE LOADING
    # ──────────────────────────────────────────────────────────────
    def load_code(self):
        if not os.path.exists(self.code_path):
            raise FileNotFoundError(f"Code file not found: {self.code_path}")

        with open(self.code_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()

        code_lines = source.splitlines(keepends=True)
        total_orig = len(code_lines)

        # Gutter width based on max line-number digits
        if self.show_line_numbers:
            sample = str(total_orig) + "  "
            self._gutter_width = _tw(self.font, sample)
        else:
            self._gutter_width = 0

        sep_extra = 7 if self.show_line_numbers else 0
        usable_w  = (self.resolution[0] - 2 * self.padding
                     - self._gutter_width - sep_extra)
        self.chars_per_line = max(10, usable_w // self.char_width)
        self._text_x_start  = self.padding + self._gutter_width + sep_extra

        # Tokenize whole file at once
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

    # ──────────────────────────────────────────────────────────────
    #  PYGMENTS TOKENISATION  — pixel-perfect VS Code Dark+ mapping
    # ──────────────────────────────────────────────────────────────
    def _tokenize_source(self, source: str) -> List[List[Tuple]]:
        """Return a list-per-original-line of (RGB_color, text_fragment) tuples."""
        if not PYGMENTS_OK:
            lines = source.splitlines()
            return [[(C_PLAIN, ln)] for ln in lines]

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

            # Comments
            if ttype in Token.Comment:
                color = C_COMMENT

            # Doc-strings (green like comments in VS Code)
            elif ttype is Token.Literal.String.Doc or ttype in Token.Literal.String.Doc:
                color = C_COMMENT

            # Strings
            elif ttype in Token.Literal.String:
                color = C_STRING

            # Numbers
            elif ttype in Token.Literal.Number:
                color = C_NUMBER

            # ── Keywords: most-specific subtype FIRST ────────────
            elif ttype is Token.Keyword.Constant or ttype in Token.Keyword.Constant:
                color = C_BLUE      # True / False / None

            elif ttype is Token.Keyword.Namespace or ttype in Token.Keyword.Namespace:
                color = C_BLUE      # import / from

            elif ttype is Token.Keyword.Type or ttype in Token.Keyword.Type:
                color = C_BLUE

            elif ttype is Token.Keyword or ttype in Token.Keyword:
                if ttext in _STORAGE_KW:
                    color = C_BLUE      # def / class / lambda
                elif ttext in _CONSTANT_KW or ttext in _NAMESPACE_KW:
                    color = C_BLUE      # fallback for True/False/None/import/from
                elif ttext in _CONTROL_KW:
                    color = C_PURPLE    # if / for / while / return / …
                else:
                    color = C_PURPLE

            # Operator.Word: and / or / not / in / is
            elif ttype is Token.Operator.Word:
                color = C_BLUE

            elif ttype in Token.Operator or ttype in Token.Punctuation:
                color = C_PLAIN

            # self / cls
            elif ttype is Token.Name.Builtin.Pseudo or ttype in Token.Name.Builtin.Pseudo:
                color = C_BLUE

            # Built-in functions / types
            elif ttype is Token.Name.Builtin or ttype in Token.Name.Builtin:
                if ttext in _TYPE_BUILTINS:
                    color = C_BLUE
                elif _next_nws(idx) == "(":
                    color = C_YELLOW
                else:
                    color = C_BLUE

            # Name subtypes — most specific first
            elif ttype in Token.Name.Function.Magic:
                color = C_YELLOW
            elif ttype in Token.Name.Function:
                color = C_YELLOW
            elif ttype in Token.Name.Class:
                color = C_TEAL
            elif ttype in Token.Name.Exception:
                color = C_TEAL
            elif ttype in Token.Name.Namespace:
                color = C_TEAL
            elif ttype in Token.Name.Decorator:
                color = C_YELLOW
            elif ttype in Token.Name.Constant:
                color = C_BRIGHT_BLUE
            elif ttype in Token.Name.Attribute:
                color = C_LIGHT_BLUE
            elif ttype in Token.Name.Variable.Magic:
                color = C_LIGHT_BLUE
            elif ttype in Token.Name.Variable:
                color = C_LIGHT_BLUE

            # Generic Token.Name — context aware
            elif ttype is Token.Name or ttype in Token.Name:
                nw = _next_nws(idx)
                pw = _prev_nws(idx)
                if nw == "(":
                    color = C_YELLOW
                elif pw == ".":
                    color = C_LIGHT_BLUE
                elif ttext.isupper() and len(ttext) > 1 and not ttext.isdigit():
                    color = C_BRIGHT_BLUE
                else:
                    color = C_LIGHT_BLUE

            else:
                color = _pygments_color(ttype)

            flat.append((color, ttext))

        # Split flat token list back into per-original-line lists
        per_line: List[List[Tuple]] = [[]]
        for color, ttext in flat:
            parts = ttext.split("\n")
            for k, part in enumerate(parts):
                if k > 0:
                    per_line.append([])
                if part:
                    per_line[-1].append((color, part))

        return per_line

    # ──────────────────────────────────────────────────────────────
    #  TOKEN SLICING  (B3 fixed: advance by len(wrapped_text) not 1)
    # ──────────────────────────────────────────────────────────────
    def _slice_tokens(
        self,
        line_tokens: List[Tuple],
        wrapped_text: str,
        full_line: str,
        wrap_pos: int = 0,
    ) -> List[Tuple]:
        """
        Extract the colour runs that correspond to *wrapped_text* inside
        *full_line*, selecting the wrap_pos-th occurrence.

        BUG-FIX: original advanced search_start by 1, which could land
        inside the previous match when the match is longer than 1 char.
        We now advance by len(wrapped_text) to skip past the full match.
        """
        if not line_tokens:
            return [(C_PLAIN, wrapped_text)] if wrapped_text else []
        if not wrapped_text:
            return []

        # Find the wrap_pos-th occurrence
        search_start = 0
        found_idx    = -1
        for _ in range(wrap_pos + 1):
            idx = full_line.find(wrapped_text, search_start)
            if idx == -1:
                break
            found_idx    = idx
            # FIX: advance past the whole match, not just one character
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

    # ──────────────────────────────────────────────────────────────
    #  DURATION CONTROL
    # ──────────────────────────────────────────────────────────────
    def _set_exact_duration(self, target_seconds: float):
        total_chars = sum(len(l) for l in self.wrapped_lines)
        total_lines = len(self.wrapped_lines)
        available   = target_seconds - FINAL_PAUSE_SECONDS

        if available <= 0 or total_chars + total_lines == 0:
            return

        # Preserve original ratio between char_delay and line_delay
        k           = self.line_delay / max(self.char_delay, 1e-9)
        denominator = total_chars + total_lines * k
        d           = available / denominator

        self.char_delay = max(0.005, min(0.5, d))
        self.line_delay = max(0.010, min(2.0, d * k))

        actual = (total_chars * self.char_delay
                  + total_lines * self.line_delay
                  + FINAL_PAUSE_SECONDS)
        print(f"[Duration] target={target_seconds:.1f}s  actual~{actual:.1f}s  "
              f"char_delay={self.char_delay:.4f}s  line_delay={self.line_delay:.4f}s")

    def estimated_duration(self) -> float:
        total_chars = sum(len(l) for l in self.wrapped_lines)
        total_lines = len(self.wrapped_lines)
        return (total_chars * self.char_delay
                + total_lines * self.line_delay
                + FINAL_PAUSE_SECONDS)

    # ──────────────────────────────────────────────────────────────
    #  VIEWPORT  (B6 fixed: clamped smooth-scroll step)
    # ──────────────────────────────────────────────────────────────
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
            # FIX B6: clamp step so we never overshoot
            step = diff * 0.25
            step = max(-abs(diff), min(abs(diff), step))   # sign-correct clamp
            self.viewport_offset += step
        else:
            self.viewport_offset = float(target)
        self.viewport_offset = max(0.0, min(self.viewport_offset, max_offset))

    # ──────────────────────────────────────────────────────────────
    #  FRAME RENDERING
    # ──────────────────────────────────────────────────────────────
    def create_frame(
        self,
        cur_line:       int,
        cur_char:       int,
        cursor_visible: bool = True,
    ) -> np.ndarray:
        """
        Render one video frame.
        *cur_line* / *cur_char* describe how much has been "typed" AND
        where the cursor sits (they are the same concept).
        """
        draw = self._frame_draw
        draw.rectangle([0, 0, *self.resolution], fill=self.background_color)

        start_line = int(self.viewport_offset)
        end_line   = min(start_line + self.max_visible_lines,
                         len(self.wrapped_lines))

        # Gutter separator
        if self.show_line_numbers and self._gutter_width > 0:
            sep_x = self.padding + self._gutter_width + 3
            draw.line(
                [(sep_x, self.padding),
                 (sep_x, self.resolution[1] - self.padding)],
                fill=self._gutter_border,
                width=1,
            )

        cursor_px_x = self._text_x_start   # pixel x for cursor on cur_line

        for i in range(start_line, end_line):
            y = self.padding + (i - start_line) * self.line_height_px
            if y + self.line_height_px > self.resolution[1] - self.padding:
                break

            line   = self.wrapped_lines[i]
            tokens = self.tokenized_lines[i]

            # ── Line numbers ──────────────────────────────────────
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

            # ── Code text ─────────────────────────────────────────
            if i < cur_line:
                # Fully typed line
                for color, tok in tokens:
                    if not self.syntax_highlighting:
                        color = self.text_color
                    draw.text((x, y), tok, font=self.font, fill=color)
                    x += _tw(self.font, tok)

            elif i == cur_line:
                # Partially typed line — draw only cur_char characters
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
                cursor_px_x = x   # remember pixel position for cursor

        # ── Cursor ────────────────────────────────────────────────
        if (self.show_cursor and cursor_visible
                and start_line <= cur_line < end_line):
            cy = self.padding + (cur_line - start_line) * self.line_height_px
            if self.animation_type == "typewriter":
                # Underline bar
                draw.rectangle(
                    [cursor_px_x,
                     cy + self.line_height_px - 3,
                     cursor_px_x + self.char_width,
                     cy + self.line_height_px - 1],
                    fill=self.cursor_color,
                )
            else:
                # Thin vertical bar (VS Code default)
                draw.rectangle(
                    [cursor_px_x,
                     cy,
                     cursor_px_x + 2,
                     cy + self.line_height_px - 2],
                    fill=self.cursor_color,
                )

        # ── Scrollbar ─────────────────────────────────────────────
        if len(self.wrapped_lines) > self.max_visible_lines:
            self._draw_scrollbar(draw, start_line)

        return cv2.cvtColor(np.array(self._frame_img), cv2.COLOR_RGB2BGR)

    def _draw_scrollbar(self, draw: ImageDraw.Draw, start_line: int):
        sw      = 6
        sx      = self.resolution[0] - self.padding // 2 - sw
        total   = len(self.wrapped_lines)
        visible = self.max_visible_lines
        track_h = self.resolution[1] - 2 * self.padding

        thumb_h = max(20, int(track_h * visible / total))
        ratio   = start_line / max(1, total - visible)
        thumb_y = self.padding + int((track_h - thumb_h) * ratio)

        draw.rectangle(
            [sx, self.padding, sx + sw, self.resolution[1] - self.padding],
            fill=(50, 50, 55))
        draw.rectangle(
            [sx, thumb_y, sx + sw, thumb_y + thumb_h],
            fill=(110, 110, 120))

    # ──────────────────────────────────────────────────────────────
    #  FRAME COUNT   (B4 fixed: +1 per line to match render loop)
    # ──────────────────────────────────────────────────────────────
    def total_frames(self) -> int:
        # render loop: range(len(line)+1) → (len+1) * frames_per_char per line
        fpc = max(1, int(self.char_delay * self.fps))
        fpl = max(1, int(self.line_delay * self.fps))   # B2 fixed here too
        char_frames  = sum((len(l) + 1) * fpc for l in self.wrapped_lines)
        line_frames  = len(self.wrapped_lines) * fpl
        pause_frames = int(FINAL_PAUSE_SECONDS * self.fps)
        return char_frames + line_frames + pause_frames

    # ──────────────────────────────────────────────────────────────
    #  MAIN RENDER LOOP
    # ──────────────────────────────────────────────────────────────
    def render(
        self,
        progress_callback=None,
        cancel_flag: Optional[threading.Event] = None,
    ) -> "str | bool":
        """
        Write all frames to *self.output_path*.
        Returns the output path on success, False on failure/cancellation.
        """
        if progress_callback:
            progress_callback(0, 100, "Initialising…", 0)

        # B7: try hardware-friendly codec first, fall back to mp4v
        writer = None
        for fourcc_str in ("avc1", "mp4v"):
            fourcc = cv2.VideoWriter_fourcc(*fourcc_str)
            w_try  = cv2.VideoWriter(
                self.output_path, fourcc, self.fps, self.resolution)
            if w_try.isOpened():
                writer = w_try
                break
            w_try.release()

        if writer is None or not writer.isOpened():
            if progress_callback:
                progress_callback(0, 100, "Error: cannot open video writer", 0)
            return False

        # B2 fixed: guarantee at least 1 frame per inter-line pause
        frames_per_char = max(1, int(self.char_delay * self.fps))
        frames_per_line = max(1, int(self.line_delay * self.fps))
        blink_period    = max(1, self.fps // 3)

        n_total    = self.total_frames()
        n_written  = 0
        cur_line   = 0
        cur_char   = 0
        self.viewport_offset = 0.0

        start_time = time.time()

        def cancelled() -> bool:
            return cancel_flag is not None and cancel_flag.is_set()

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
            for line_idx, line in enumerate(self.wrapped_lines):
                if cancelled():
                    abort()
                    return False

                # Type each character (including one extra position for cursor-at-end)
                for char_idx in range(len(line) + 1):
                    for _ in range(frames_per_char):
                        write_one()
                    if char_idx < len(line):
                        cur_char += 1

                # Inter-line pause
                for _ in range(frames_per_line):
                    if cancelled():
                        abort()
                        return False
                    write_one()

                # Advance to next line
                cur_line += 1
                cur_char  = 0

            # Final hold
            for _ in range(int(FINAL_PAUSE_SECONDS * self.fps)):
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

    # ──────────────────────────────────────────────────────────────
    #  OPEN VIDEO
    # ──────────────────────────────────────────────────────────────
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


# ══════════════════════════════════════════════════════════════════════
#  GUI  — VS Code Dark+ theme throughout, no colour customisation needed
# ══════════════════════════════════════════════════════════════════════
class TypewriterVideoApp:
    """
    Tkinter front-end.

    B1 fixed: _load_settings() now runs AFTER all tk.Vars are created.
    B5 fixed: info panel uses renderer's actual chars_per_line.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Code Typewriter — VS Code Dark+")
        self.root.geometry("1150x800")

        # ── All tk.Vars created FIRST ─────────────────────────────
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

        self.video_info_var     = tk.StringVar(value="No file loaded")
        self.estimated_time_var = tk.StringVar(value="Estimated: --")
        self.target_time_var    = tk.StringVar(value="Target: 30 s")

        # Rendering state
        self.render_thread        = None
        self.cancel_flag          = threading.Event()
        self.current_code_content = "# Load a file to preview"
        self._preview_timer       = None
        self._cached_wrap_key     = None
        self._cached_wrapped      = []

        # B1 FIX: settings loaded AFTER vars are ready
        self._load_settings()
        self._build_ui()

    # ──────────────────────────────────────────────────────────────
    #  SETTINGS PERSISTENCE
    # ──────────────────────────────────────────────────────────────
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
        except (json.JSONDecodeError, KeyError, ValueError):
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
            }
            with open(SETTINGS_FILE, "w") as fh:
                json.dump(s, fh, indent=2)
        except OSError:
            pass

    # ──────────────────────────────────────────────────────────────
    #  UI BUILD
    # ──────────────────────────────────────────────────────────────
    def _build_ui(self):
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
            ("Video",     self._tab_video),
            ("Animation", self._tab_animation),
            ("Duration",  self._tab_duration),
        ]:
            frame = ttk.Frame(nb)
            nb.add(frame, text=label)
            builder(frame)

        self._build_preview(right)
        self._build_info_bar(right)
        self._build_progress_bar(main)

    # ── Tab: File ─────────────────────────────────────────────────
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

    # ── Tab: Video ────────────────────────────────────────────────
    def _tab_video(self, p):
        def labeled_scale(parent, label, var, lo, hi):
            ttk.Label(parent, text=label,
                      font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(10, 4))
            f = ttk.Frame(parent); f.pack(fill=tk.X, padx=5)
            lbl = ttk.Label(f, width=5); lbl.pack(side=tk.RIGHT)
            def on_change(v):
                lbl.config(text=str(int(float(v))))
                self._schedule_preview()
            s = tk.Scale(f, from_=lo, to=hi, variable=var,
                         orient=tk.HORIZONTAL, showvalue=0, command=on_change)
            s.pack(fill=tk.X, expand=True)
            lbl.config(text=str(var.get()))

        ttk.Label(p, text="Resolution", font=("Arial", 10, "bold")).pack(
            anchor=tk.W, pady=(10, 4))
        ttk.Combobox(
            p, textvariable=self.resolution_var,
            values=["640x480", "1280x720", "1920x1080", "2560x1440"],
            state="readonly",
        ).pack(fill=tk.X, padx=5)

        labeled_scale(p, "Frames per second", self.fps_var,      15, 60)
        labeled_scale(p, "Font size (px)",    self.font_size_var, 12, 36)

        ttk.Separator(p, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5, pady=12)
        ttk.Checkbutton(p, text="Syntax highlighting (VS Code Dark+)",
                        variable=self.syntax_highlight_var,
                        command=self._schedule_preview).pack(anchor=tk.W)
        ttk.Checkbutton(p, text="Show line numbers",
                        variable=self.show_line_numbers_var,
                        command=self._schedule_preview).pack(anchor=tk.W, pady=4)

    # ── Tab: Animation ────────────────────────────────────────────
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
        ttk.Checkbutton(p, text="Follow cursor  (auto-scroll)",
                        variable=self.follow_cursor_var).pack(anchor=tk.W)
        ttk.Checkbutton(p, text="Smooth scrolling",
                        variable=self.smooth_scroll_var).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(p, text="Show cursor",
                        variable=self.show_cursor_var).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(p, text="Blinking cursor",
                        variable=self.cursor_blink_var).pack(anchor=tk.W, pady=2)

    # ── Tab: Duration ─────────────────────────────────────────────
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

    # ── Preview ───────────────────────────────────────────────────
    def _build_preview(self, parent):
        pf = ttk.LabelFrame(parent, text="Preview  (VS Code Dark+)", padding=6)
        pf.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas = tk.Canvas(pf, bg="#1e1e1e", highlightthickness=0)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.root.after(100, self._update_preview)

    def _schedule_preview(self, *_):
        if self._preview_timer:
            self.root.after_cancel(self._preview_timer)
        self._preview_timer = self.root.after(350, self._update_preview)

    def _update_preview(self):
        try:
            self._render_preview()
        except Exception:
            pass   # never crash the UI thread from preview

    def _render_preview(self):
        self.preview_canvas.delete("all")
        try:
            w, h = map(int, self.resolution_var.get().split("x"))
        except ValueError:
            w, h = 1280, 720
        pw = max(100, int(w * PREVIEW_SCALE))
        ph = max(60,  int(h * PREVIEW_SCALE))

        img  = Image.new("RGB", (pw, ph), VSCODE_BG)
        draw = ImageDraw.Draw(img)

        fs   = max(7, int(self.font_size_var.get() * PREVIEW_SCALE))
        font = self._load_preview_font(fs)

        cw = max(1, _tw(font, "M"))
        lh = max(8, int(_th(font, "Mg") * 1.35))

        show_gutter = self.show_line_numbers_var.get()
        gutter_w    = cw * 4 if show_gutter else 0
        sep_x       = 4 + gutter_w + 2 if show_gutter else None
        text_x0     = 4 + gutter_w + (5 if show_gutter else 0)

        if sep_x is not None:
            draw.line([(sep_x, 4), (sep_x, ph - 4)],
                      fill=VSCODE_GUTTER_BORDER, width=1)

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
                flat: List[Tuple] = []
                for idx, (ttype, ttext) in enumerate(raw):
                    flat.append((_pygments_color(ttype), ttext))
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

        lines = source.split("\n")
        y = 4
        for lineno, line in enumerate(lines[:PREVIEW_MAX_LINES], 1):
            if y + lh > ph:
                break
            if show_gutter:
                ns  = str(lineno)
                nw  = _tw(font, ns)
                draw.text((4 + gutter_w - nw - 2, y), ns,
                          font=font, fill=VSCODE_GUTTER_FG)

            x     = text_x0
            max_x = pw - 4

            if tok_per_line and (lineno - 1) < len(tok_per_line):
                for color, ttext in tok_per_line[lineno - 1]:
                    if not self.syntax_highlight_var.get():
                        color = C_PLAIN
                    if x >= max_x:
                        break
                    tw = _tw(font, ttext)
                    if x + tw > max_x:
                        chars_fit = max(0, (max_x - x) // cw)
                        ttext = ttext[:chars_fit]
                    if ttext:
                        draw.text((x, y), ttext, font=font, fill=color)
                    x += _tw(font, ttext)
            else:
                chars_fit = max(0, (max_x - x) // cw)
                draw.text((x, y), line[:chars_fit], font=font, fill=C_PLAIN)

            y += lh

        # Cursor
        draw.rectangle([text_x0, y, text_x0 + 2, y + lh - 2],
                       fill=VSCODE_CURSOR)

        self._preview_photo = ImageTk.PhotoImage(img)
        self.preview_canvas.create_image(0, 0, anchor=tk.NW,
                                         image=self._preview_photo)
        self.preview_canvas.config(width=pw, height=ph)

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

    # ── Info bar ──────────────────────────────────────────────────
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

    # ── Progress & controls ───────────────────────────────────────
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

    # ──────────────────────────────────────────────────────────────
    #  HELPER: wrapped lines using renderer's actual chars_per_line
    # ──────────────────────────────────────────────────────────────
    def _get_wrapped_lines(self) -> List[str]:
        """
        B5 fix: use the renderer's chars_per_line rather than a hard-coded 80.
        We approximate chars_per_line from font metrics here to avoid fully
        constructing the renderer just for estimation.
        """
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
        cw        = max(1, _tw(font, "M"))
        gutter_w  = cw * 6 if self.show_line_numbers_var.get() else 0
        usable_w  = w - 2 * padding - gutter_w
        cpp       = max(10, usable_w // cw)   # chars per line

        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            wrapped = []
            for line in lines:
                w_list = textwrap.wrap(
                    line.rstrip("\n"), width=cpp,
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
        total_chars = sum(len(l) for l in wrapped)
        total_lines = len(wrapped)

        cd = self.char_delay_var.get()
        ld = self.line_delay_var.get()

        if self.use_target_duration.get() and self.target_duration_var.get() > 0:
            target = self.target_duration_var.get()
            avail  = target - FINAL_PAUSE_SECONDS
            if avail > 0 and total_chars + total_lines > 0:
                k  = ld / max(cd, 1e-9)
                d  = avail / (total_chars + total_lines * k)
                cd = max(0.005, min(0.5, d))
                ld = max(0.01,  min(2.0, d * k))

        est = total_chars * cd + total_lines * ld + FINAL_PAUSE_SECONDS
        self.video_info_var.set(
            f"Lines: {total_lines:,}  |  Chars: {total_chars:,}"
            f"  |  FPS: {self.fps_var.get()}")
        self.estimated_time_var.set(f"Estimated: {est:.1f} s")
        self.target_time_var.set(
            f"Target: {self.target_duration_var.get():.0f} s")

    # ──────────────────────────────────────────────────────────────
    #  FILE DIALOGS
    # ──────────────────────────────────────────────────────────────
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
        self.code_file.set(fname)
        try:
            with open(fname, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
            self.current_code_content = content[:PREVIEW_CHAR_LIMIT]
        except OSError as e:
            messagebox.showerror("Error reading file", str(e))
            return
        self._schedule_preview()
        self._update_info()
        if not self.output_file.get():
            base    = os.path.splitext(os.path.basename(fname))[0]
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            out_dir = desktop if os.path.exists(desktop) else "."
            self.output_file.set(os.path.join(out_dir, f"{base}_typewriter.mp4"))

    def _browse_output(self):
        fname = filedialog.asksaveasfilename(
            title="Save video as",
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4"), ("All files", "*.*")])
        if fname:
            self.output_file.set(fname)

    # ──────────────────────────────────────────────────────────────
    #  RENDERING
    # ──────────────────────────────────────────────────────────────
    def _start(self):
        fpath = self.code_file.get()
        if not fpath or not os.path.exists(fpath):
            messagebox.showerror("Missing file", "Please select a valid code file.")
            return
        if not self.output_file.get():
            messagebox.showerror("Missing output", "Please specify an output path.")
            return
        try:
            map(int, self.resolution_var.get().split("x"))
        except ValueError:
            messagebox.showerror("Bad resolution",
                                 "Use WxH format, e.g. 1280x720")
            return

        self._save_settings()
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.cancel_flag.clear()
        self.render_thread = threading.Thread(
            target=self._render_worker, daemon=True)
        self.render_thread.start()

    def _render_worker(self):
        try:
            w, h = map(int, self.resolution_var.get().split("x"))
        except ValueError:
            w, h = 1280, 720

        try:
            renderer = CodeTypewriterVideoRenderer(
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
            )
            result = renderer.render(
                progress_callback = self._on_progress,
                cancel_flag       = self.cancel_flag,
            )
        except Exception as exc:
            result = None
            self.root.after(0, lambda: messagebox.showerror("Render error", str(exc)))

        self.root.after(0, self._on_done, result)

    def _on_progress(self, percent, _total, status, eta):
        self.root.after(0, self._apply_progress, percent, status, eta)

    def _apply_progress(self, percent, status, eta):
        self.progress_var.set(percent)
        self.status_var.set(
            f"{status}   ETA: {eta:.0f} s" if eta > 0 else status)

    def _cancel(self):
        self.cancel_flag.set()
        self.status_var.set("Cancelling…")

    def _on_done(self, result):
        self.start_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)

        if isinstance(result, str) and os.path.exists(result):
            self.progress_var.set(100)
            self.status_var.set("Done!")
            if self.auto_open_var.get():
                if messagebox.askyesno(
                        "Render complete",
                        f"Video saved:\n{os.path.abspath(result)}\n\nOpen now?"):
                    try:
                        sys_name = platform.system()
                        if sys_name == "Windows":
                            os.startfile(os.path.abspath(result))
                        elif sys_name == "Darwin":
                            subprocess.call(["open", os.path.abspath(result)])
                        else:
                            subprocess.call(["xdg-open", os.path.abspath(result)])
                    except OSError as e:
                        messagebox.showinfo("Saved", f"Saved to:\n{result}\n\n{e}")
            else:
                messagebox.showinfo(
                    "Render complete", f"Saved:\n{os.path.abspath(result)}")

        elif not self.cancel_flag.is_set():
            self.status_var.set("Rendering failed.")
            messagebox.showerror(
                "Error", "Could not create video. Check the console for details.")
        else:
            self.status_var.set("Cancelled.")
            self.progress_var.set(0)


# ══════════════════════════════════════════════════════════════════════
def main():
    root = tk.Tk()
    root.update_idletasks()
    w, h = 1150, 800
    x = (root.winfo_screenwidth()  // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")
    TypewriterVideoApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()