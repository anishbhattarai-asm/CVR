"""Colours: the `Palette` the renderer paints with, and VS Code theme parsing."""

import json
import os
import re
from typing import List, Optional, Tuple, Union

RGB = Tuple[int, int, int]


def hex_to_rgb(hex_str: str) -> RGB:
    """"#RRGGBB" (or "#RRGGBBAA", alpha ignored) to an (r, g, b) tuple."""
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


_h = hex_to_rgb

C_PLAIN       = _h("#D4D4D4")
C_COMMENT     = _h("#6A9955")
C_STRING      = _h("#CE9178")
C_NUMBER      = _h("#B5CEA8")
C_PURPLE      = _h("#C586C0")
C_BLUE        = _h("#569CD6")
C_YELLOW      = _h("#DCDCAA")
C_TEAL        = _h("#4EC9B0")
C_LIGHT_BLUE  = _h("#9CDCFE")
C_BRIGHT_BLUE = _h("#4FC1FF")

VSCODE_BG            = _h("#1E1E1E")
VSCODE_GUTTER_FG     = _h("#858585")
VSCODE_GUTTER_ACTIVE = _h("#C6C6C6")
VSCODE_GUTTER_BORDER = _h("#404040")
VSCODE_CURSOR        = _h("#AEAFAD")


def strip_jsonc(text: str) -> str:
    """Theme files are JSON with comments and trailing commas."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 2; continue
            if c == '"':
                in_str = False
            i += 1; continue
        if c == '"':
            in_str = True; out.append(c); i += 1; continue
        if c == "/" and i + 1 < n:
            if text[i + 1] == "/":
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if text[i + 1] == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2; continue
        out.append(c); i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def read_jsonc(path: str):
    """Load a JSON-with-comments file such as a theme or settings.json."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return json.loads(strip_jsonc(fh.read()))


class Palette:
    """Every colour the renderer draws with, as RGB tuples.

    Defaults reproduce VS Code Dark+; `from_vscode_theme` replaces them with
    the real values out of an installed theme. Any field can also be given
    as a "#RRGGBB" string.
    """

    FIELDS = (
        "bg", "fg", "gutter_fg", "gutter_active", "gutter_border", "cursor",
        "comment", "string", "number", "constant", "other_constant",
        "keyword_ctrl", "storage", "operator", "operator_word",
        "function", "builtin_fn", "type_", "namespace", "variable",
        "language_var", "decorator",
        "tab_bg", "tab_active_bg", "tab_active_fg", "tab_border",
        "tab_accent",
    )

    def __init__(self, name: str = "Bundled Dark Plus",
                 **kw: Union[RGB, str, None]):
        unknown = set(kw) - set(self.FIELDS)
        if unknown:
            raise TypeError(f"unknown palette field(s): {', '.join(sorted(unknown))}")
        self.name = name
        values = dict(
            bg            = VSCODE_BG,
            fg            = C_PLAIN,
            gutter_fg     = VSCODE_GUTTER_FG,
            gutter_active = VSCODE_GUTTER_ACTIVE,
            gutter_border = VSCODE_GUTTER_BORDER,
            cursor        = VSCODE_CURSOR,
            comment       = C_COMMENT,
            string        = C_STRING,
            number        = C_NUMBER,
            constant      = C_BLUE,
            other_constant= C_BRIGHT_BLUE,
            keyword_ctrl  = C_PURPLE,
            storage       = C_BLUE,
            operator      = C_PLAIN,
            operator_word = C_BLUE,
            function      = C_YELLOW,
            builtin_fn    = C_YELLOW,
            type_         = C_TEAL,
            namespace     = C_TEAL,
            variable      = C_LIGHT_BLUE,
            language_var  = C_BLUE,
            decorator     = C_YELLOW,
            tab_bg        = _h("#252526"),
            tab_active_bg = VSCODE_BG,
            tab_active_fg = _h("#FFFFFF"),
            tab_border    = _h("#2A2B2C"),
            tab_accent    = _h("#0078D4"),
        )
        values.update({k: v for k, v in kw.items() if v is not None})
        for field in self.FIELDS:
            value = values[field]
            setattr(self, field, _h(value) if isinstance(value, str)
                    else tuple(value))

    def __repr__(self) -> str:
        return f"Palette({self.name!r})"

    def replace(self, name: Optional[str] = None,
                **kw: Union[RGB, str]) -> "Palette":
        """A copy with some colours changed, e.g. `pal.replace(bg="#000000")`."""
        values = {f: getattr(self, f) for f in self.FIELDS}
        values.update(kw)
        return Palette(name or self.name, **values)

    # VS Code theme loading

    @classmethod
    def _read_theme(cls, path: str, depth: int = 0):
        """Return (colors, tokenColors) with any `include` chain resolved.

        An included theme is the *base*: its rules come first so the including
        theme's rules win, which is how VS Code resolves them.
        """
        data = read_jsonc(path)

        colors, rules = {}, []
        include = data.get("include")
        if include and depth < 8:
            base = os.path.normpath(os.path.join(os.path.dirname(path), include))
            if os.path.isfile(base):
                colors, rules = cls._read_theme(base, depth + 1)

        colors = {**colors, **data.get("colors", {})}
        return colors, rules + list(data.get("tokenColors", []))

    @staticmethod
    def _resolve(rules: List[dict], scope: str) -> Optional[str]:
        """Longest matching TextMate selector wins; ties go to the later rule."""
        best, best_len = None, -1
        for rule in rules:
            selectors = rule.get("scope", [])
            if isinstance(selectors, str):
                selectors = selectors.split(",")
            for sel in selectors:
                sel = sel.strip()
                # Descendant selectors ("source.cpp keyword") only apply in
                # context we don't model, so score them by their last part.
                sel = sel.split()[-1] if sel else sel
                if sel and (scope == sel or scope.startswith(sel + ".")):
                    if len(sel) >= best_len:
                        fg = (rule.get("settings") or {}).get("foreground")
                        if fg:
                            best, best_len = fg, len(sel)
        return best

    @classmethod
    def from_vscode_theme(cls, label: str, path: str) -> "Palette":
        """Build a palette from a VS Code colour theme json file."""
        colors, rules = cls._read_theme(path)

        def col(key, fallback=None):
            v = colors.get(key)
            return _h(v) if isinstance(v, str) and len(v.lstrip("#")) >= 6 else fallback

        def tok(scope, fallback=None):
            v = cls._resolve(rules, scope)
            return _h(v) if isinstance(v, str) and len(v.lstrip("#")) >= 6 else fallback

        fg = col("editor.foreground") or tok("source") or C_PLAIN
        return cls(
            name          = label,
            bg            = col("editor.background"),
            fg            = fg,
            gutter_fg     = col("editorLineNumber.foreground"),
            gutter_active = col("editorLineNumber.activeForeground"),
            gutter_border = col("editorIndentGuide.background1",
                                col("editorIndentGuide.background")),
            cursor        = col("editorCursor.foreground", fg),
            comment       = tok("comment"),
            string        = tok("string"),
            number        = tok("constant.numeric"),
            constant      = tok("constant.language"),
            other_constant= tok("variable.other.constant", tok("constant.other")),
            keyword_ctrl  = tok("keyword.control", tok("keyword")),
            storage       = tok("storage.type", tok("keyword")),
            operator      = tok("keyword.operator", fg),
            operator_word = tok("keyword.operator.logical.python",
                                tok("keyword.operator.expression")),
            function      = tok("entity.name.function"),
            builtin_fn    = tok("support.function", tok("entity.name.function")),
            type_         = tok("entity.name.type", tok("support.type")),
            namespace     = tok("entity.name.namespace", tok("entity.name.type")),
            variable      = tok("variable", fg),
            language_var  = tok("variable.language", tok("constant.language")),
            decorator     = tok("entity.name.function.decorator",
                                tok("entity.name.function")),
            tab_bg        = col("editorGroupHeader.tabsBackground",
                                col("tab.inactiveBackground")),
            tab_active_bg = col("tab.activeBackground", col("editor.background")),
            tab_active_fg = col("tab.activeForeground", fg),
            tab_border    = col("editorGroupHeader.tabsBorder", col("tab.border")),
            tab_accent    = col("tab.activeBorderTop", col("tab.activeBorder")),
        )

    @classmethod
    def from_file(cls, path: str, name: Optional[str] = None) -> "Palette":
        """Palette from any theme json, named after the file unless told otherwise."""
        label = name or os.path.splitext(os.path.basename(path))[0]
        return cls.from_vscode_theme(label, path)


DEFAULT_PALETTE = Palette()
