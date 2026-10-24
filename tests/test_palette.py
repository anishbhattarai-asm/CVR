import json

import pytest

from cvr.palette import DEFAULT_PALETTE, Palette, hex_to_rgb, strip_jsonc

from conftest import write_json


def test_strip_jsonc_removes_comments_and_trailing_commas():
    text = '''{
        // line comment
        "a": "http://not-a-comment", /* block
        comment */ "b": [1, 2,],
        "c": "quote \\" // still a string",
    }'''
    assert json.loads(strip_jsonc(text)) == {
        "a": "http://not-a-comment", "b": [1, 2], "c": 'quote " // still a string'}


def test_hex_to_rgb_ignores_alpha():
    assert hex_to_rgb("#1E1E1E") == (30, 30, 30)
    assert hex_to_rgb("ff000080") == (255, 0, 0)


def test_palette_accepts_hex_strings_and_rejects_unknown_fields():
    pal = Palette("x", bg="#000000", fg=(1, 2, 3))
    assert pal.bg == (0, 0, 0) and pal.fg == (1, 2, 3)
    assert pal.comment == DEFAULT_PALETTE.comment
    with pytest.raises(TypeError):
        Palette("x", background="#000000")


def test_replace_returns_a_modified_copy():
    pal = DEFAULT_PALETTE.replace(bg="#101010")
    assert pal.bg == (16, 16, 16)
    assert DEFAULT_PALETTE.bg == (30, 30, 30)
    assert pal.name == DEFAULT_PALETTE.name


def test_resolve_prefers_longest_selector_then_later_rule():
    rules = [
        {"scope": "keyword", "settings": {"foreground": "#111111"}},
        {"scope": ["keyword.control"], "settings": {"foreground": "#222222"}},
        {"scope": "keyword.control", "settings": {"foreground": "#333333"}},
        {"scope": "source.cpp keyword.operator", "settings": {"foreground": "#444444"}},
        {"scope": "string, comment", "settings": {"foreground": "#555555"}},
    ]
    assert Palette._resolve(rules, "keyword.control.flow") == "#333333"
    assert Palette._resolve(rules, "keyword.other") == "#111111"
    assert Palette._resolve(rules, "keyword.operator") == "#444444"
    assert Palette._resolve(rules, "comment") == "#555555"
    assert Palette._resolve(rules, "keywordish") is None


def test_theme_include_chain_lets_the_child_win(tmp_path):
    write_json(str(tmp_path / "base.json"), {
        "colors": {"editor.background": "#000000", "editor.foreground": "#AAAAAA"},
        "tokenColors": [
            {"scope": "comment", "settings": {"foreground": "#00FF00"}},
            {"scope": "string", "settings": {"foreground": "#FF0000"}},
        ],
    })
    child = write_json(str(tmp_path / "child.json"), {
        "include": "./base.json",
        "colors": {"editor.background": "#101010"},
        "tokenColors": [{"scope": "string", "settings": {"foreground": "#0000FF"}}],
    }, header="// themes may start with a comment\n")

    pal = Palette.from_file(child)
    assert pal.name == "child"
    assert pal.bg == (16, 16, 16)          # child overrides base
    assert pal.fg == (170, 170, 170)       # inherited from base
    assert pal.comment == (0, 255, 0)      # inherited rule
    assert pal.string == (0, 0, 255)       # later rule wins
    assert pal.cursor == pal.fg            # falls back to the foreground
