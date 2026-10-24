"""Finding the colour themes VS Code has installed on this machine."""

import difflib
import os
import platform
from typing import Iterable, List, NamedTuple, Optional

from .palette import DEFAULT_PALETTE, Palette, read_jsonc

LIGHT_SUFFIX = "  (light)"


class ThemeInfo(NamedTuple):
    """One installed theme: the label VS Code shows and its json file."""
    label: str
    path: str


def vscode_extension_roots() -> List[str]:
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


def list_themes() -> List[ThemeInfo]:
    """Every colour theme found, sorted by label.

    Light themes carry a "  (light)" suffix on the label.
    """
    found, seen = [], set()
    for root in vscode_extension_roots():
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
                pkg = read_jsonc(pkg_json)
            except (OSError, ValueError):
                continue

            themes = (pkg.get("contributes") or {}).get("themes") or []
            if not themes:
                continue

            nls = {}
            nls_path = os.path.join(pkg_dir, "package.nls.json")
            if os.path.isfile(nls_path):
                try:
                    nls = read_jsonc(nls_path)
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
                found.append(ThemeInfo(f"{label}{'' if dark else LIGHT_SUFFIX}", path))

    found.sort(key=lambda t: t.label.lower())
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
            data = read_jsonc(path)
        except (OSError, ValueError):
            continue
        label = data.get("workbench.colorTheme") or label
    return label


def _match(themes: Iterable[ThemeInfo], wanted: str) -> Optional[ThemeInfo]:
    """Exact label first, then the first label that starts with `wanted`."""
    themes = list(themes)
    for t in themes:
        if t.label == wanted:
            return t
    for t in themes:
        if t.label.startswith(wanted):
            return t
    return None


def default_theme(themes: Optional[List[ThemeInfo]] = None) -> Optional[ThemeInfo]:
    """The theme VS Code is set to, else its stock dark theme, else None."""
    if themes is None:
        themes = list_themes()
    active = None
    try:
        active = active_vscode_theme_label()
    except Exception:
        pass
    # No explicit setting means VS Code's own default; newer builds ship
    # "Dark 2026", older ones "Dark Modern".
    for wanted in (active, "Dark 2026", "Dark Modern", "Dark+"):
        if wanted:
            found = _match(themes, wanted)
            if found:
                return found
    return None


def find_theme(name: str,
               themes: Optional[List[ThemeInfo]] = None) -> Optional[ThemeInfo]:
    """Look an installed theme up by label, ignoring case and the light suffix."""
    if themes is None:
        themes = list_themes()

    def norm(label: str) -> str:
        if label.endswith(LIGHT_SUFFIX):
            label = label[:-len(LIGHT_SUFFIX)]
        return label.strip().lower()

    wanted = norm(name)
    for t in themes:
        if norm(t.label) == wanted:
            return t
    for t in themes:
        if norm(t.label).startswith(wanted):
            return t
    return None


def load_theme(theme: Optional[str] = None) -> Palette:
    """Turn a theme reference into a `Palette`.

    `theme` may be:

    * None, for the theme VS Code is currently set to (falling back to its
      stock dark theme, then to the bundled Dark+ palette);
    * the label of an installed theme, e.g. "Dark Modern" or "Monokai";
    * a path to a theme json file;
    * "Bundled Dark Plus", for the built-in palette that needs no VS Code.

    Raises LookupError when a named theme is not installed.
    """
    if theme is None:
        found = default_theme()
        return Palette.from_vscode_theme(*found) if found else DEFAULT_PALETTE

    if os.path.isfile(theme):
        return Palette.from_file(theme)

    if theme.strip().lower() == DEFAULT_PALETTE.name.lower():
        return DEFAULT_PALETTE

    themes = list_themes()
    found = find_theme(theme, themes)
    if found:
        return Palette.from_vscode_theme(*found)

    labels = [t.label for t in themes] + [DEFAULT_PALETTE.name]
    close = difflib.get_close_matches(theme, labels, n=3, cutoff=0.5)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise LookupError(f"No installed VS Code theme named {theme!r}.{hint} "
                      "Run `cvr themes` to list them.")
