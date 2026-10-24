"""Small windows the app pops up: the done notification and the player chooser."""

import os
import platform
import tkinter as tk
from tkinter import filedialog, ttk
from typing import List, Optional, Tuple

from ..palette import DEFAULT_PALETTE
from ..players import Player

TOAST_SECONDS = 25                  # how long the "render complete" card sits


class Toast:
    """Small click-me card in the bottom-right corner.

    Deliberately not a messagebox: a modal dialog steals focus from whatever
    the user moved on to while the render ran.  This one waits to be clicked
    and gives up after TOAST_SECONDS.
    """

    def __init__(self, root: tk.Tk, title: str, body: str, hint: str,
                 on_click, palette=None):
        self.root     = root
        self.on_click = on_click
        self._fade    = None
        self._timer   = None

        pal = palette or DEFAULT_PALETTE
        bg     = "#%02X%02X%02X" % pal.bg
        fg     = "#%02X%02X%02X" % pal.fg
        accent = "#%02X%02X%02X" % pal.function
        muted  = "#%02X%02X%02X" % pal.comment

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

        outer = tk.Frame(self.win, bg=accent, padx=1, pady=1)
        outer.pack(fill=tk.BOTH, expand=True)
        card = tk.Frame(outer, bg=bg, padx=16, pady=12)
        card.pack(fill=tk.BOTH, expand=True)

        head = tk.Frame(card, bg=bg)
        head.pack(fill=tk.X)
        tk.Label(head, text=title, bg=bg, fg=accent,
                 font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        close = tk.Label(head, text="✕", bg=bg, fg=muted,
                         font=("Segoe UI", 10), cursor="hand2")
        close.pack(side=tk.RIGHT)
        close.bind("<Button-1>", lambda _e: self.close())

        tk.Label(card, text=body, bg=bg, fg=fg, justify=tk.LEFT,
                 wraplength=330, font=("Segoe UI", 9)).pack(
                     anchor=tk.W, pady=(6, 2))
        tk.Label(card, text=hint, bg=bg, fg=muted, justify=tk.LEFT,
                 font=("Segoe UI", 9, "italic")).pack(anchor=tk.W)

        for w in [self.win, outer, card] + list(card.winfo_children()):
            if w is close:
                continue
            w.bind("<Button-1>", self._clicked)
            try:
                w.config(cursor="hand2")
            except tk.TclError:
                pass
        for w in head.winfo_children():
            if w is not close:
                w.bind("<Button-1>", self._clicked)

        self.win.update_idletasks()
        w = max(300, self.win.winfo_reqwidth())
        h = self.win.winfo_reqheight()
        x = self.root.winfo_screenwidth() - w - 24
        y = self.root.winfo_screenheight() - h - 80
        self.win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        self.win.deiconify()
        self._fade_in(0.0)
        self._timer = self.root.after(TOAST_SECONDS * 1000, self.close)

    def _fade_in(self, alpha: float):
        if not self._alive():
            return
        try:
            self.win.attributes("-alpha", min(alpha, 0.97))
        except tk.TclError:
            return
        if alpha < 0.97:
            self._fade = self.root.after(15, self._fade_in, alpha + 0.12)

    def _alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _clicked(self, _event=None):
        callback = self.on_click
        self.close()
        if callback:
            callback()

    def close(self):
        for job in (self._fade, self._timer):
            if job:
                try:
                    self.root.after_cancel(job)
                except (tk.TclError, ValueError):
                    pass
        self._fade = self._timer = None
        if self._alive():
            try:
                self.win.destroy()
            except tk.TclError:
                pass


class PlayerChooser:
    """Modal 'open this video with…' list.  Returns (Player, remember)."""

    def __init__(self, parent: tk.Misc, players: List[Player],
                 video_path: str, preselect: Optional[Player] = None):
        self.players  = list(players)
        self.result: Optional[Player] = None
        self.remember = tk.BooleanVar(value=False)

        self.win = tk.Toplevel(parent)
        self.win.title("Open video with…")
        self.win.transient(parent.winfo_toplevel())
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

        frame = ttk.Frame(self.win, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Play the rendered video with",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)
        ttk.Label(frame, text=os.path.basename(video_path),
                  font=("Segoe UI", 8)).pack(anchor=tk.W, pady=(0, 8))

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(list_frame, height=min(9, max(4, len(self.players))),
                                  width=42, activestyle="dotbox",
                                  exportselection=False,
                                  yscrollcommand=scroll.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.config(command=self.listbox.yview)
        self._fill_list(preselect)
        self.listbox.bind("<Double-Button-1>", lambda _e: self._open())
        self.listbox.bind("<Return>", lambda _e: self._open())

        ttk.Checkbutton(frame, text="Always use this player (skip this dialog)",
                        variable=self.remember).pack(anchor=tk.W, pady=(10, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="Browse…", command=self._browse).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="▶  Open", command=self._open).pack(
            side=tk.RIGHT, padx=(0, 8))

        self.win.bind("<Escape>", lambda _e: self._cancel())
        self.win.update_idletasks()
        top = parent.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - self.win.winfo_width()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.win.winfo_height()) // 3
        self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.win.grab_set()
        self.listbox.focus_set()

    def _fill_list(self, preselect: Optional[Player]):
        self.listbox.delete(0, tk.END)
        for p in self.players:
            self.listbox.insert(tk.END, p.name)
        index = 0
        if preselect:
            for i, p in enumerate(self.players):
                if p.kind == preselect.kind and (
                        p.target == preselect.target or p.name == preselect.name):
                    index = i
                    break
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(index)
        self.listbox.see(index)

    def _browse(self):
        if platform.system() == "Windows":
            types = [("Programs", "*.exe"), ("All files", "*.*")]
        else:
            types = [("All files", "*.*")]
        path = filedialog.askopenfilename(parent=self.win,
                                          title="Pick a video player",
                                          filetypes=types)
        if not path:
            return
        kind = "app" if path.endswith(".app") else "exe"
        name = os.path.splitext(os.path.basename(path.rstrip("/\\")))[0]
        player = Player(name, kind, path)
        self.players.append(player)
        self._fill_list(player)

    def _open(self):
        sel = self.listbox.curselection()
        self.result = self.players[sel[0]] if sel else self.players[0]
        self._destroy()

    def _cancel(self):
        self.result = None
        self._destroy()

    def _destroy(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def show(self) -> Tuple[Optional[Player], bool]:
        self.win.wait_window()
        return self.result, bool(self.remember.get())
