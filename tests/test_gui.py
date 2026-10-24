"""The GUI needs a display to run, so this only checks it imports and that
settings persist. Skipped where Tkinter is missing."""

import json

import pytest

pytest.importorskip("tkinter")

from cvr.gui import TypewriterVideoApp, settings  # noqa: E402


def test_app_imports():
    assert callable(TypewriterVideoApp)


def test_settings_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "settings.json"
    monkeypatch.setenv("CVR_SETTINGS", str(path))
    settings.save({"fps": 60, "theme": "Monokai"})
    assert json.loads(path.read_text()) == {"fps": 60, "theme": "Monokai"}
    assert settings.load() == {"fps": 60, "theme": "Monokai"}


def test_settings_fall_back_to_the_old_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CVR_SETTINGS", str(tmp_path / "new" / "settings.json"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / settings.LEGACY_NAME).write_text('{"font_size": 22}')
    assert settings.load() == {"font_size": 22}
