"""The desktop app: pick a file and a theme, preview it, render it."""

import logging
import os
import platform
import textwrap
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageTk

from ..fonts import load_font, text_height, text_width
from ..highlight import tokenize
from ..palette import DEFAULT_PALETTE, Palette
from ..players import Player, discover_players
from ..renderer import (FINAL_PAUSE_SECONDS, TAB_WIDTH, Renderer,
                        count_typed_chars, solve_delays)
from ..themes import ThemeInfo, default_theme, list_themes
from . import settings
from .widgets import PlayerChooser, Toast

log = logging.getLogger(__name__)

PREVIEW_CHAR_LIMIT  = 3000
PREVIEW_MAX_W       = 600
PREVIEW_MAX_H       = 340

ASK_EACH_TIME  = "Ask each time"     # player_choice value: pop the chooser


class TypewriterVideoApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CVR — Code Video Renderer")
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
        self._toast               = None

        # Which player opens the finished video.  `None` means "ask", i.e.
        # the notification opens the chooser.
        try:
            self.players = discover_players()
        except Exception as exc:
            log.warning("Player discovery failed: %s", exc)
            self.players = [Player("System default", "default")]
        self.preferred_player: Optional[Player] = None
        self.player_choice_var = tk.StringVar(value=ASK_EACH_TIME)

        # Every theme VS Code has locally, plus the bundled fallback.
        self.themes: List[ThemeInfo] = [ThemeInfo(DEFAULT_PALETTE.name, "")]
        try:
            self.themes += list_themes()
        except Exception as exc:
            log.warning("Theme discovery failed: %s", exc)
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
        found = default_theme(self.themes)
        return found.label if found else self.themes[0].label

    def _palette_for(self, label: str) -> "Palette":
        path = dict(self.themes).get(label, "")
        if path in self._palette_cache:
            return self._palette_cache[path]
        try:
            pal = Palette.from_vscode_theme(label, path)
        except Exception as exc:
            log.warning("Could not read theme %r: %s", label, exc)
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
        s = settings.load()
        if not s:
            return
        try:
            self.fps_var.set(s.get("fps", 30))
            self.font_size_var.set(s.get("font_size", 18))
            self.target_duration_var.set(s.get("target_duration", 30.0))
            self.char_delay_var.set(s.get("char_delay", 0.05))
            self.line_delay_var.set(s.get("line_delay", 0.30))
            self.resolution_var.set(s.get("resolution", "1280x720"))
            self.filename_bar_var.set(s.get("filename_bar", True))
            self.instant_indent_var.set(s.get("instant_indent", True))
            self.exact_colour_var.set(s.get("exact_colour", False))
            self.auto_open_var.set(s.get("auto_open", True))
            self._set_preferred_player(Player.from_dict(s.get("player")))
            saved_theme = s.get("theme")
            if saved_theme and saved_theme in [n for n, _ in self.themes]:
                self.theme_var.set(saved_theme)
        except (KeyError, TypeError, ValueError, tk.TclError):
            pass

    def _save_settings(self):
        settings.save({
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
            "auto_open":       self.auto_open_var.get(),
            "player": (self.preferred_player.as_dict()
                       if self.preferred_player else None),
        })

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

        ttk.Checkbutton(p, text="Notify me when the render is done",
                        variable=self.auto_open_var).pack(anchor=tk.W, pady=8)

        ttk.Label(p, text="Play with", font=("Arial", 10, "bold")).pack(
            anchor=tk.W, pady=(6, 4))
        ttk.Label(p, text="“Ask each time” lets the notification open the "
                          "player chooser.", font=("Arial", 8)).pack(
                              anchor=tk.W, padx=5)
        f3 = ttk.Frame(p); f3.pack(fill=tk.X, padx=5, pady=4)
        self.player_combo = ttk.Combobox(
            f3, textvariable=self.player_choice_var, state="readonly",
            values=self._player_choice_values())
        self.player_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.player_combo.bind("<<ComboboxSelected>>", self._on_player_choice)
        ttk.Button(f3, text="Browse…", command=self._browse_player).pack(
            side=tk.RIGHT)

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
        ttk.Checkbutton(p, text="Syntax highlighting",
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
        font = load_font(None, fs)

        cw = max(1.0, text_width(font, "M"))
        lh = max(8, int(text_height(font, "Mg") * 1.35))

        show_gutter = self.show_line_numbers_var.get()
        gutter_w    = int(cw * 4) if show_gutter else 0
        sep_x       = 4 + gutter_w + 2 if show_gutter else None
        text_x0     = 4 + gutter_w + (5 if show_gutter else 0)

        bar_h = 0
        if self.filename_bar_var.get():
            bar_h = max(10, int(lh * 1.9))
            name  = os.path.basename(self.code_file.get()) or "untitled"
            tab_w = int(text_width(font, name) + max(8, cw * 3))
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

        # Same highlighter as the render, so the preview colours match it.
        if self.syntax_highlight_var.get() and source.strip():
            try:
                tok_per_line = tokenize(source, pal,
                                        filename=self.code_file.get() or None)
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
                nw  = text_width(font, ns)
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
                    tw = text_width(font, ttext)
                    if x + tw > max_x:
                        chars_fit = max(0, int((max_x - x) // cw))
                        ttext = ttext[:chars_fit]
                    if ttext:
                        draw.text((x, y), ttext, font=font, fill=color)
                    x += text_width(font, ttext)
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
        font      = load_font(None, self.font_size_var.get())
        cw        = max(1.0, text_width(font, "M"))
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
        total_chars = count_typed_chars(wrapped, self.instant_indent_var.get())
        total_lines = len(wrapped)

        cd = self.char_delay_var.get()
        ld = self.line_delay_var.get()

        if self.use_target_duration.get() and self.target_duration_var.get() > 0:
            cd, ld = solve_delays(total_chars, total_lines, cd, ld,
                                  self.target_duration_var.get())

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
            renderer = Renderer(**cfg)
            result = renderer.render(
                progress_callback = self._on_progress,
                cancel_flag       = self.cancel_flag,
            )
            if not result:
                self._render_error = renderer.error
        except Exception as exc:
            self._render_error = str(exc)
            log.error("Render error: %s", exc)

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
        if self._toast:
            self._toast.close()
            self._toast = None
        if self._preview_timer:
            try:
                self.root.after_cancel(self._preview_timer)
            except tk.TclError:
                pass
        self.root.destroy()

    # ---- picking and launching a player -------------------------------

    def _player_choice_values(self) -> List[str]:
        names = [ASK_EACH_TIME] + [p.name for p in self.players]
        if self.preferred_player and self.preferred_player.name not in names:
            names.append(self.preferred_player.name)
        return names

    def _set_preferred_player(self, player: Optional[Player]):
        """Remember `player` (None = ask each time) and sync the combobox."""
        self.preferred_player = player
        if player and all(p.target != player.target or p.kind != player.kind
                          for p in self.players):
            self.players.append(player)
        self.player_choice_var.set(player.name if player else ASK_EACH_TIME)
        combo = getattr(self, "player_combo", None)
        if combo is not None:
            combo.config(values=self._player_choice_values())

    def _on_player_choice(self, _event=None):
        name = self.player_choice_var.get()
        if name == ASK_EACH_TIME:
            self.preferred_player = None
            return
        for p in self.players:
            if p.name == name:
                self.preferred_player = p
                return

    def _browse_player(self):
        types = ([("Programs", "*.exe"), ("All files", "*.*")]
                 if platform.system() == "Windows" else [("All files", "*.*")])
        path = filedialog.askopenfilename(title="Pick a video player",
                                          filetypes=types)
        if not path:
            return
        kind = "app" if path.endswith(".app") else "exe"
        name = os.path.splitext(os.path.basename(path.rstrip("/\\")))[0]
        self._set_preferred_player(Player(name, kind, path))

    def _notify_render_complete(self, result: str):
        """Corner card instead of a modal box, so it never steals focus."""
        size_mb = os.path.getsize(result) / (1024 * 1024)
        target = (self.preferred_player.name if self.preferred_player
                  else "choose a player")
        if self._toast:
            self._toast.close()
        try:
            self._toast = Toast(
                self.root,
                "Render complete",
                f"{os.path.basename(result)}  ·  {size_mb:.1f} MB",
                f"Click to play — {target}",
                lambda: self._play_result(result),
                self.palette,
            )
        except tk.TclError:
            self._toast = None
            if messagebox.askyesno("Render complete",
                                   f"Video saved:\n{os.path.abspath(result)}"
                                   "\n\nOpen now?"):
                self._play_result(result)

    def _play_result(self, result: str):
        """Notification was clicked: pick a player (or use the saved one)."""
        if not os.path.exists(result):
            self._safe_message("error", "Missing file",
                               f"The video is gone:\n{result}")
            return

        player = self.preferred_player
        if player is None:
            player, remember = PlayerChooser(
                self.root, self.players, result,
                preselect=self.preferred_player).show()
            if player is None:
                return                      # cancelled: leave the file alone
            if remember:
                self._set_preferred_player(player)
                self._save_settings()
        self._open_output_file(result, player)

    def _open_output_file(self, result: str, player: Optional[Player] = None):
        player = player or self.preferred_player or Player("System default",
                                                           "default")
        try:
            player.launch(result)
            self.status_var.set(f"Playing in {player.name}")
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
                self._notify_render_complete(result)
            else:
                self._safe_message(
                    "info",
                    "Render complete",
                    f"Saved:\n{os.path.abspath(result)}",
                )

        elif not self.cancel_flag.is_set():
            self.status_var.set("Rendering failed.")
            detail = self._render_error or "Could not create video."
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
