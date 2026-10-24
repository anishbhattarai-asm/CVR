"""The typewriter renderer: source code in, video frames out."""

import logging
import math
import os
import textwrap
import time
from typing import Callable, Iterator, List, Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageDraw

from .encoders import open_writer
from .fonts import load_font, text_width
from .highlight import Line, tokenize
from .palette import DEFAULT_PALETTE, Palette
from .themes import load_theme

log = logging.getLogger(__name__)

TAB_WIDTH           = 4      # PIL draws "\t" as one blank glyph, so expand it
FINAL_PAUSE_SECONDS = 2.0

# Animation style -> (char_delay, line_delay). "smooth" uses the delays given.
DELAY_PRESETS = {
    "fast":       (0.02, 0.10),
    "typewriter": (0.10, 0.50),
}
ANIMATION_STYLES = ("smooth", "fast", "typewriter")

# progress(percent, total, status, eta_seconds); total is always 100.
ProgressCallback = Callable[[float, float, str, float], None]
PathLike = Union[str, "os.PathLike[str]"]


class RenderError(RuntimeError):
    """A render failed. The message says why."""


class RenderCancelled(RenderError):
    """A render was stopped through its cancel event."""


def count_typed_chars(lines: List[str], instant_indent: bool = True) -> int:
    """Characters that are actually typed out.

    Leading indentation appears in one go, the way an editor indents,
    so it must not be counted in the timing either.
    """
    if instant_indent:
        return sum(len(l.lstrip()) for l in lines)
    return sum(len(l) for l in lines)


def solve_delays(total_chars: int, total_lines: int, char_delay: float,
                 line_delay: float, target_seconds: float) -> Tuple[float, float]:
    """Scale both delays so the video lasts `target_seconds`.

    Keeps the ratio between them and leaves them alone when the target
    cannot be met (shorter than the final pause, or nothing to type).
    """
    available = target_seconds - FINAL_PAUSE_SECONDS
    if available <= 0 or total_chars + total_lines == 0:
        return char_delay, line_delay

    k = line_delay / max(char_delay, 1e-9)
    d = available / (total_chars + total_lines * k)
    return max(0.0002, min(0.5, d)), max(0.0010, min(2.0, d * k))


class Renderer:
    """Renders one source file (or string) as a typewriter video.

    Construction reads and lays out the code; `render()` writes the video,
    `frames()` yields the frames for you to do something else with.
    """

    def __init__(
        self,
        code_path:          Optional[PathLike]    = None,
        output_path:        Optional[PathLike]    = None,
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
        palette:            Optional[Palette]     = None,
        show_filename_bar:  bool                  = True,
        instant_indent:     bool                  = True,
        exact_colour:       bool                  = True,
        source:             Optional[str]         = None,
        language:           Optional[str]         = None,
    ):
        if code_path is None and source is None:
            raise ValueError("give a code_path, a source string, or both")
        code_path   = os.fspath(code_path) if code_path is not None else None
        output_path = os.fspath(output_path) if output_path is not None else None
        if animation_type not in ANIMATION_STYLES:
            raise ValueError(f"animation_type must be one of {ANIMATION_STYLES}")

        self.palette             = palette or DEFAULT_PALETTE
        self.show_filename_bar   = show_filename_bar
        self.instant_indent      = instant_indent
        self.exact_colour        = exact_colour
        self.code_path           = code_path
        self.source              = source
        self.language            = language
        self.syntax_highlighting = syntax_highlighting
        self.show_line_numbers   = show_line_numbers
        self.follow_cursor       = follow_cursor
        self.smooth_scroll       = smooth_scroll
        self.animation_type      = animation_type
        self.target_duration     = target_duration
        self.show_cursor         = show_cursor
        self.cursor_blink        = cursor_blink
        self.error: Optional[str] = None

        self.display_name = os.path.basename(code_path) if code_path else "untitled"
        if output_path is None:
            stem = os.path.splitext(self.display_name)[0] or "code"
            self.output_path = os.path.abspath(f"{stem}_typewriter.mp4")
        else:
            self.output_path = output_path

        self.resolution = tuple(resolution)
        self.fps        = fps
        self.font_size  = font_size
        self.padding    = 20

        self.char_delay, self.line_delay = DELAY_PRESETS.get(
            animation_type, (char_delay, line_delay))

        pal = self.palette
        self.background_color = pal.bg
        self.text_color       = pal.fg
        self.cursor_color     = pal.cursor
        self._gutter_fg       = pal.gutter_fg
        self._gutter_active   = pal.gutter_active
        self._gutter_border   = pal.gutter_border

        self.font = load_font(font_path, font_size)

        _cap_bb   = self.font.getbbox("M")
        _full_bb  = self.font.getbbox("Mg")
        self.char_width     = max(1.0, text_width(self.font, "M"))
        self.char_height    = max(1, _cap_bb[3] - _cap_bb[1])
        self.line_height_px = max(1, int((_full_bb[3] - _full_bb[1]) * 1.35))

        # Editor tab across the top. Capped so a short frame keeps room
        # for the code.
        self._bar_h = (min(int(self.line_height_px * 1.9),
                           max(0, self.resolution[1] // 4))
                       if self.show_filename_bar else 0)
        if self._bar_h < 6:
            self._bar_h = 0
            self.show_filename_bar = False
        self._text_y_start = self._bar_h + self.padding

        self.viewport_offset = 0.0
        screen_h = max(1, self.resolution[1] - self._text_y_start - self.padding)
        self.max_visible_lines = max(1, screen_h // self.line_height_px)

        self.wrapped_lines:   List[str]          = []
        self.tokenized_lines: List[Line]         = []
        self.line_numbers:    List[Optional[int]] = []
        self._gutter_width    = 0
        self._text_x_start    = self.padding
        self._cur_line        = 0

        self._frame_img  = Image.new("RGB", self.resolution, self.background_color)
        self._frame_draw = ImageDraw.Draw(self._frame_img)

        self.load_code()

        if self.target_duration > 0:
            self._set_exact_duration(self.target_duration)

    def _read_source(self) -> str:
        if self.source is not None:
            return self.source
        if not os.path.exists(self.code_path):
            raise FileNotFoundError(f"Code file not found: {self.code_path}")
        with open(self.code_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def load_code(self):
        """Read, highlight and wrap the code to fit the frame."""
        source = self._read_source().expandtabs(TAB_WIDTH)

        code_lines = source.splitlines(keepends=True)
        total_orig = len(code_lines)

        if self.show_line_numbers:
            sample = str(max(total_orig, 1)) + "  "
            self._gutter_width = int(math.ceil(text_width(self.font, sample)))
        else:
            self._gutter_width = 0

        sep_extra = 7 if self.show_line_numbers else 0
        usable_w  = (self.resolution[0] - 2 * self.padding
                     - self._gutter_width - sep_extra)
        self.chars_per_line = max(10, int(usable_w // self.char_width))
        self._text_x_start  = self.padding + self._gutter_width + sep_extra

        per_line_tokens = tokenize(source, self.palette,
                                   filename=self.code_path,
                                   language=self.language)

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

    def _slice_tokens(
        self,
        line_tokens: Line,
        wrapped_text: str,
        full_line: str,
        wrap_pos: int = 0,
    ) -> Line:
        plain = self.palette.fg
        if not line_tokens:
            return [(plain, wrapped_text)] if wrapped_text else []
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
            return [(plain, wrapped_text)]

        start = found_idx
        end   = start + len(wrapped_text)

        result: Line = []
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

        return result or [(plain, wrapped_text)]

    def _typed_chars(self) -> int:
        return count_typed_chars(self.wrapped_lines, self.instant_indent)

    def _set_exact_duration(self, target_seconds: float):
        total_chars = self._typed_chars()
        total_lines = len(self.wrapped_lines)
        self.char_delay, self.line_delay = solve_delays(
            total_chars, total_lines, self.char_delay, self.line_delay,
            target_seconds)
        log.info("Duration: target=%.1fs  actual~%.1fs  char_delay=%.4fs  "
                 "line_delay=%.4fs", target_seconds, self.estimated_duration(),
                 self.char_delay, self.line_delay)

    def estimated_duration(self) -> float:
        """Length of the video in seconds, final pause included."""
        total_chars = self._typed_chars()
        total_lines = len(self.wrapped_lines)
        return (total_chars * self.char_delay
                + total_lines * self.line_delay
                + FINAL_PAUSE_SECONDS)

    def total_frames(self) -> int:
        return max(1, int(round(self.estimated_duration() * self.fps)))

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
        """Draw one frame: lines before `cur_line` in full, `cur_char`
        characters of `cur_line`, nothing after. Returns an RGB array."""
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

            tokens = self.tokenized_lines[i]

            # A line's number appears once the cursor reaches it, as it
            # would while typing in an editor.
            if self.show_line_numbers and self._gutter_width > 0 and i <= cur_line:
                lineno = self.line_numbers[i]
                if lineno is not None:
                    num_str   = str(lineno)
                    num_w     = text_width(self.font, num_str)
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
                    x += text_width(self.font, tok)

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
                        x     += text_width(self.font, tok)
                        drawn += tok_len
                    else:
                        partial = tok[:cur_char - drawn]
                        draw.text((x, y), partial, font=self.font, fill=color)
                        x += text_width(self.font, partial)
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

        return np.array(self._frame_img)

    def _draw_filename_bar(self, draw: ImageDraw.ImageDraw):
        """The editor tab across the top, in the theme's own tab colours."""
        pal   = self.palette
        h     = self._bar_h
        width = self.resolution[0]

        draw.rectangle([0, 0, width, h], fill=pal.tab_bg)

        name  = self.display_name
        pad_x = max(12, int(self.char_width * 1.5))
        tab_w = int(text_width(self.font, name) + pad_x * 2)

        draw.rectangle([0, 0, tab_w, h], fill=pal.tab_active_bg)
        accent_h = max(2, int(h * 0.07))
        draw.rectangle([0, 0, tab_w, accent_h], fill=pal.tab_accent)
        draw.line([(tab_w, 0), (tab_w, h - 1)], fill=pal.tab_border)
        draw.line([(0, h - 1), (width, h - 1)], fill=pal.tab_border)

        text_y = accent_h + max(0, (h - accent_h - self.line_height_px) // 2)
        draw.text((pad_x, text_y), name, font=self.font, fill=pal.tab_active_fg)

    def _draw_scrollbar(self, draw: ImageDraw.ImageDraw, start_line: int):
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

    def frames(self) -> Iterator[np.ndarray]:
        """Yield every frame of the video in order, as RGB uint8 arrays of
        shape (height, width, 3). About `total_frames()` of them."""
        blink_period = max(1, self.fps // 3)
        frame_debt   = 0.0
        n_drawn      = 0
        self.viewport_offset = 0.0
        self._cur_line = 0

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

        def frame(cur_line: int, cur_char: int) -> np.ndarray:
            nonlocal n_drawn
            self._tick_viewport(self._desired_viewport(cur_line))
            blink_on = ((n_drawn // blink_period) % 2 == 0
                        if self.cursor_blink else True)
            n_drawn += 1
            return self.create_frame(cur_line, cur_char,
                                     cursor_visible=self.show_cursor and blink_on)

        for cur_line, line in enumerate(self.wrapped_lines):
            self._cur_line = cur_line
            # Indentation lands in one go, the way an editor indents.
            indent = (len(line) - len(line.lstrip())
                      if self.instant_indent else 0)

            for char_idx in range(indent, len(line)):
                for _ in range(frames_for(self.char_delay)):
                    yield frame(cur_line, char_idx + 1)

            for _ in range(frames_for(self.line_delay)):
                yield frame(cur_line, len(line))

        done = len(self.wrapped_lines)
        self._cur_line = done
        for _ in range(max(1, frames_for(FINAL_PAUSE_SECONDS))):
            yield frame(done, 0)

    def render(
        self,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_flag=None,
    ) -> Union[str, bool]:
        """Write the video to `output_path`.

        Returns the output path, or False on failure or cancel (the reason is
        left in `self.error`). `cancel_flag` is anything with `is_set()`, such
        as a threading.Event; setting it stops the render and deletes the
        partial file. So does Ctrl+C, which is re-raised.
        """
        self.error = None

        def fail(message: str) -> bool:
            self.error = message
            log.error("Render failed: %s", message)
            if progress_callback:
                progress_callback(0, 100, f"Error: {message}", 0)
            return False

        if progress_callback:
            progress_callback(0, 100, "Initialising...", 0)

        out_dir = os.path.dirname(os.path.abspath(self.output_path))
        if out_dir and not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as exc:
                return fail(str(exc))

        writer = open_writer(self.output_path, self.fps, self.resolution,
                             exact_colour=self.exact_colour)
        if writer is None:
            return fail("cannot open a video writer (no ffmpeg and no OpenCV)")

        n_total    = self.total_frames()
        n_lines    = len(self.wrapped_lines)
        start_time = time.time()

        def abort():
            writer.release()
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except OSError:
                    pass

        try:
            for n_written, frame in enumerate(self.frames(), 1):
                if cancel_flag is not None and cancel_flag.is_set():
                    abort()
                    self.error = "cancelled"
                    return False
                writer.write(frame)
                if progress_callback and n_written % 15 == 0:
                    pct     = min(100.0, n_written / max(n_total, 1) * 100)
                    elapsed = time.time() - start_time
                    eta     = max(0.0, (elapsed / n_written) * (n_total - n_written))
                    progress_callback(
                        pct, 100,
                        f"Line {min(self._cur_line + 1, n_lines)}/{n_lines}", eta)
        except KeyboardInterrupt:
            abort()
            raise
        except Exception as exc:
            abort()
            return fail(str(exc))

        writer.release()

        if os.path.exists(self.output_path):
            size_mb = os.path.getsize(self.output_path) / (1024 * 1024)
            if progress_callback:
                progress_callback(100, 100, f"Done! ({size_mb:.1f} MB)", 0)
            return self.output_path
        return fail("the encoder finished without writing a file")

    def open_video(self) -> bool:
        """Open the rendered video in the system's default player."""
        from .players import Player
        if not os.path.exists(self.output_path):
            return False
        try:
            Player("System default", "default").launch(self.output_path)
            return True
        except OSError as e:
            log.warning("Could not open video: %s", e)
            return False


# The name the renderer had before CVR became a package.
CodeTypewriterVideoRenderer = Renderer


def render(
    code_path:  Optional[PathLike] = None,
    output_path: Optional[PathLike] = None,
    *,
    theme:      Union[str, Palette, None] = None,
    duration:   Optional[float] = None,
    source:     Optional[str] = None,
    language:   Optional[str] = None,
    progress:   Optional[ProgressCallback] = None,
    cancel=None,
    **options,
) -> str:
    """Render code to a typewriter video and return the video's path.

    `theme` is an installed VS Code theme's name, a theme json path, a
    `Palette`, or None for the theme VS Code is currently set to.
    `duration` fixes the length in seconds; leave it out to let the
    per character and per line delays decide. Any other keyword goes
    straight to `Renderer` (resolution, fps, font_size, ...).

    Raises RenderError if the render fails and RenderCancelled if `cancel`
    is set before it finishes.
    """
    palette = theme if isinstance(theme, Palette) else load_theme(theme)
    renderer = Renderer(code_path, output_path, palette=palette,
                        source=source, language=language,
                        target_duration=duration or 0.0, **options)
    result = renderer.render(progress_callback=progress, cancel_flag=cancel)
    if result:
        return result
    if cancel is not None and cancel.is_set():
        raise RenderCancelled("render cancelled")
    raise RenderError(renderer.error or "render failed")
