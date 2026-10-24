"""Video players installed on this machine, for opening a finished render."""

import os
import platform
import shutil
import subprocess
from typing import List, Optional


class Player:
    """One way to play a file: the OS default, an .exe, or a macOS .app."""

    __slots__ = ("name", "kind", "target")

    def __init__(self, name: str, kind: str, target: str = ""):
        self.name   = name          # what the user sees
        self.kind   = kind          # "default" | "exe" | "app"
        self.target = target        # path to the exe / .app ("" for default)

    def __repr__(self) -> str:
        return f"Player({self.name!r}, {self.kind!r}, {self.target!r})"

    def launch(self, path: str):
        path = os.path.abspath(path)
        if self.kind == "exe":
            subprocess.Popen([self.target, path])
        elif self.kind == "app":
            subprocess.Popen(["open", "-a", self.target, path])
        elif platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def as_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "target": self.target}

    @staticmethod
    def from_dict(d) -> Optional["Player"]:
        try:
            kind = d["kind"]
            target = d.get("target", "")
        except (TypeError, KeyError):
            return None
        if kind in ("exe", "app") and not os.path.exists(target):
            return None                     # player was uninstalled or moved
        return Player(d.get("name") or os.path.basename(target) or "System default",
                      kind, target)


def _win_app_paths(exe: str) -> Optional[str]:
    """Ask the registry where an installer put exe, e.g. 'vlc.exe'."""
    try:
        import winreg
    except ImportError:
        return None
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + exe
    for root_key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for flag in (0, getattr(winreg, "KEY_WOW64_32KEY", 0)):
            try:
                with winreg.OpenKey(root_key, key, 0,
                                    winreg.KEY_READ | flag) as k:
                    val = winreg.QueryValueEx(k, "")[0].strip('"')
                if val and os.path.exists(val):
                    return val
            except OSError:
                continue
    return None


def discover_players() -> List[Player]:
    """Video players installed on this machine, default first.

    Nothing here is required: an empty list still leaves "System default",
    which is what the app did before the chooser existed.
    """
    players = [Player("System default", "default")]
    seen = set()

    def add(name: str, path: Optional[str], kind: str = "exe"):
        if not path:
            return
        key = os.path.normcase(path)
        if key in seen or not os.path.exists(path):
            return
        seen.add(key)
        players.append(Player(name, kind, path))

    system = platform.system()
    if system == "Windows":
        pf   = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            ("VLC media player", ["vlc.exe"],
             [rf"{pf}\VideoLAN\VLC\vlc.exe", rf"{pf86}\VideoLAN\VLC\vlc.exe"]),
            ("mpv", ["mpv.exe"], []),
            ("MPC-HC", ["mpc-hc64.exe", "mpc-hc.exe"],
             [rf"{pf}\MPC-HC\mpc-hc64.exe", rf"{pf86}\MPC-HC\mpc-hc.exe",
              rf"{pf}\MPC-HC64\mpc-hc64.exe",
              rf"{pf86}\K-Lite Codec Pack\MPC-HC64\mpc-hc64.exe"]),
            ("PotPlayer", ["potplayermini64.exe"],
             [rf"{pf}\DAUM\PotPlayer\PotPlayerMini64.exe",
              rf"{pf86}\DAUM\PotPlayer\PotPlayerMini.exe"]),
            ("KMPlayer", ["kmplayer64.exe"],
             [rf"{pf86}\The KMPlayer\KMPlayer.exe"]),
            ("Windows Media Player", [],
             [rf"{pf86}\Windows Media Player\wmplayer.exe",
              rf"{pf}\Windows Media Player\wmplayer.exe"]),
            ("ffplay", ["ffplay.exe"], []),
        ]
        for name, exes, paths in candidates:
            found = None
            for exe in exes:
                found = _win_app_paths(exe) or shutil.which(exe)
                if found:
                    break
            for p in ([found] if found else []) + paths:
                if p and os.path.exists(p):
                    add(name, p)
                    break
        if local:
            add("Windows Terminal-installed mpv",
                os.path.join(local, "Microsoft", "WindowsApps", "mpv.exe"))
    elif system == "Darwin":
        for name, app in [("VLC media player", "/Applications/VLC.app"),
                          ("IINA", "/Applications/IINA.app"),
                          ("mpv", "/Applications/mpv.app"),
                          ("QuickTime Player",
                           "/System/Applications/QuickTime Player.app")]:
            add(name, app, kind="app")
        for name, exe in [("mpv (command line)", "mpv"), ("ffplay", "ffplay")]:
            add(name, shutil.which(exe))
    else:
        for name, exe in [("VLC media player", "vlc"), ("mpv", "mpv"),
                          ("SMPlayer", "smplayer"), ("Celluloid", "celluloid"),
                          ("MPlayer", "mplayer"), ("Totem", "totem"),
                          ("Haruna", "haruna"), ("ffplay", "ffplay")]:
            add(name, shutil.which(exe))
    return players
