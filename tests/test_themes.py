import os

import pytest

import cvr
from cvr import themes
from cvr.themes import ThemeInfo, find_theme, load_theme

from conftest import write_json

FAKE = [
    ThemeInfo("Dark Modern", "/t/dark_modern.json"),
    ThemeInfo("Light Modern  (light)", "/t/light_modern.json"),
    ThemeInfo("Monokai", "/t/monokai.json"),
    ThemeInfo("Monokai Dimmed", "/t/monokai_dimmed.json"),
]


def test_find_theme_ignores_case_and_light_suffix():
    assert find_theme("dark modern", FAKE).label == "Dark Modern"
    assert find_theme("Light Modern", FAKE).label == "Light Modern  (light)"
    assert find_theme("Monokai", FAKE).label == "Monokai"        # exact beats prefix
    assert find_theme("monokai d", FAKE).label == "Monokai Dimmed"
    assert find_theme("Solarized", FAKE) is None


def test_default_theme_prefers_the_active_setting(monkeypatch):
    monkeypatch.setattr(themes, "active_vscode_theme_label", lambda: "Monokai")
    assert themes.default_theme(FAKE).label == "Monokai"
    monkeypatch.setattr(themes, "active_vscode_theme_label", lambda: None)
    assert themes.default_theme(FAKE).label == "Dark Modern"
    assert themes.default_theme([]) is None


def test_load_theme_accepts_a_path(tmp_path):
    path = write_json(str(tmp_path / "mine.json"),
                      {"colors": {"editor.background": "#123456"}})
    pal = load_theme(path)
    assert pal.name == "mine" and pal.bg == (0x12, 0x34, 0x56)


def test_load_theme_bundled_and_missing(monkeypatch):
    monkeypatch.setattr(themes, "list_themes", lambda: list(FAKE))
    assert load_theme("Bundled Dark Plus") is cvr.DEFAULT_PALETTE
    with pytest.raises(LookupError, match="Monokay"):
        load_theme("Monokay")


def test_list_themes_reads_extension_manifests(tmp_path, monkeypatch):
    ext = tmp_path / "extensions" / "acme.themes-1.0.0"
    write_json(str(ext / "themes" / "night.json"), {"colors": {}})
    write_json(str(ext / "themes" / "day.json"), {"colors": {}})
    write_json(str(ext / "package.json"), {"contributes": {"themes": [
        {"label": "%night%", "uiTheme": "vs-dark", "path": "./themes/night.json"},
        {"label": "Acme Day", "uiTheme": "vs", "path": "./themes/day.json"},
        {"label": "Missing", "uiTheme": "vs-dark", "path": "./themes/nope.json"},
    ]}})
    write_json(str(ext / "package.nls.json"), {"night": {"message": "Acme Night"}})
    monkeypatch.setattr(themes, "vscode_extension_roots",
                        lambda: [str(tmp_path / "extensions")])

    found = themes.list_themes()
    assert [t.label for t in found] == ["Acme Day  (light)", "Acme Night"]
    assert all(os.path.isfile(t.path) for t in found)
