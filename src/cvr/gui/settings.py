"""Where the app keeps the user's choices between launches."""

import json
import os
import platform
import sys
from typing import List

LEGACY_NAME = "typewriter_settings.json"


def settings_path() -> str:
    """settings.json in the per-user config folder for this OS.

    Not next to the code any more: once installed, that is site-packages,
    which may not be writable and is shared between users.
    """
    override = os.environ.get("CVR_SETTINGS")
    if override:
        return override
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif system == "Darwin":
        base = os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support")
    else:
        base = (os.environ.get("XDG_CONFIG_HOME")
                or os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, "cvr", "settings.json")


def _legacy_paths() -> List[str]:
    """Where the single-file CVR.py wrote its settings: next to the script,
    which for a checkout is the folder it was launched from."""
    dirs = [os.getcwd()]
    if sys.argv and sys.argv[0]:
        dirs.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    return [os.path.join(d, LEGACY_NAME) for d in dirs]


def load() -> dict:
    """Saved settings, or {} when there are none or they are unreadable.

    Falls back to an old typewriter_settings.json once, so upgrading keeps
    the user's choices; the next save writes them to the new place.
    """
    for path in [settings_path()] + _legacy_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def save(data: dict) -> None:
    path = settings_path()
    try:
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass
