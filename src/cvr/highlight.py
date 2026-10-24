"""Syntax highlighting: Pygments tokens mapped onto palette colours."""

from typing import List, Optional, Tuple

from pygments import lex
from pygments.lexer import Lexer
from pygments.lexers import (TextLexer, get_lexer_by_name,
                             get_lexer_for_filename, guess_lexer)
from pygments.token import Token

from .palette import RGB, Palette

# A line of code as (colour, text) runs.
Line = List[Tuple[RGB, str]]

_STORAGE_KW   = frozenset({"def", "class", "lambda"})
_CONSTANT_KW  = frozenset({"True", "False", "None"})

_TYPE_BUILTINS = frozenset({
    "int", "str", "float", "bool", "bytes", "complex",
    "dict", "list", "set", "tuple", "type", "object",
    "bytearray", "memoryview", "frozenset",
})

PYGMENTS_COLOR_MAP: List[Tuple] = [
    (Token.Comment,               "comment"),
    (Token.Literal.String.Doc,    "string"),
    (Token.Literal.String,        "string"),
    (Token.Literal.Number,        "number"),
    (Token.Keyword.Constant,      "constant"),
    (Token.Keyword.Namespace,     "keyword_ctrl"),
    (Token.Keyword.Type,          "storage"),
    (Token.Keyword,               "keyword_ctrl"),
    (Token.Operator.Word,         "operator_word"),
    (Token.Operator,              "operator"),
    (Token.Punctuation,           "fg"),
    (Token.Name.Builtin.Pseudo,   "language_var"),
    (Token.Name.Builtin,          "builtin_fn"),
    (Token.Name.Class,            "type_"),
    (Token.Name.Exception,        "type_"),
    (Token.Name.Namespace,        "namespace"),
    (Token.Name.Function.Magic,   "function"),
    (Token.Name.Function,         "function"),
    (Token.Name.Decorator,        "decorator"),
    (Token.Name.Attribute,        "variable"),
    (Token.Name.Variable.Magic,   "variable"),
    (Token.Name.Variable,         "variable"),
    (Token.Name.Constant,         "other_constant"),
    (Token.Name,                  "variable"),
    (Token,                       "fg"),
]


def pygments_color(ttype, pal: Palette) -> RGB:
    """Palette colour for a Pygments token type, without any context."""
    for token_cls, field in PYGMENTS_COLOR_MAP:
        if ttype is token_cls or ttype in token_cls:
            return getattr(pal, field)
    return pal.fg


def get_lexer(filename: Optional[str] = None, source: str = "",
              language: Optional[str] = None) -> Lexer:
    """Pick a lexer by language name, then file name, then by sniffing."""
    # Pygments drops leading blank lines by default, which would shift every
    # token onto the line above it.
    opts = {"stripnl": False}
    if language:
        try:
            return get_lexer_by_name(language, **opts)
        except Exception:
            pass
    if filename:
        try:
            return get_lexer_for_filename(filename, **opts)
        except Exception:
            pass
    try:
        return guess_lexer(source, **opts)
    except Exception:
        return TextLexer(**opts)


def tokenize(source: str, pal: Palette, filename: Optional[str] = None,
             language: Optional[str] = None) -> List[Line]:
    """Split `source` into lines of (colour, text) runs.

    Pygments token types are refined with a little context (a name followed
    by "(" is a call, an ALL_CAPS name is a constant) so the colours land
    where VS Code would put them.
    """
    lexer      = get_lexer(filename, source, language)
    raw_tokens = list(lex(source, lexer))
    n_raw      = len(raw_tokens)

    def _next_nws(i: int) -> str:
        for k in range(i + 1, min(i + 8, n_raw)):
            t = raw_tokens[k][1]
            if t.strip():
                return t
        return ""

    def _prev_nws(i: int) -> str:
        for k in range(i - 1, max(i - 8, -1), -1):
            t = raw_tokens[k][1]
            if t.strip():
                return t
        return ""

    flat: List[Tuple] = []

    for idx, (ttype, ttext) in enumerate(raw_tokens):

        if ttype in Token.Comment:
            color = pal.comment

        elif ttype in Token.Literal.String:
            # Docstrings are strings in VS Code, not comments.
            color = pal.string

        elif ttype in Token.Literal.Number:
            color = pal.number

        elif ttype in Token.Keyword.Constant:
            color = pal.constant

        elif ttype in Token.Keyword.Namespace:
            color = pal.keyword_ctrl

        elif ttype in Token.Keyword.Type:
            color = pal.storage

        elif ttype in Token.Keyword:
            if ttext in _STORAGE_KW:
                color = pal.storage
            elif ttext in _CONSTANT_KW:
                color = pal.constant
            else:
                color = pal.keyword_ctrl

        elif ttype is Token.Operator.Word:
            color = pal.operator_word

        elif ttype in Token.Operator or ttype in Token.Punctuation:
            color = pal.operator

        elif ttype in Token.Name.Builtin.Pseudo:
            color = pal.language_var

        elif ttype in Token.Name.Builtin:
            if ttext in _TYPE_BUILTINS:
                color = pal.type_
            elif _next_nws(idx) == "(":
                color = pal.builtin_fn
            else:
                color = pal.language_var

        elif ttype in Token.Name.Function:
            color = pal.function
        elif ttype in Token.Name.Class:
            color = pal.type_
        elif ttype in Token.Name.Exception:
            color = pal.type_
        elif ttype in Token.Name.Namespace:
            color = pal.namespace
        elif ttype in Token.Name.Decorator:
            color = pal.decorator
        elif ttype in Token.Name.Constant:
            color = pal.other_constant
        elif ttype in Token.Name.Attribute:
            color = pal.variable
        elif ttype in Token.Name.Variable:
            color = pal.variable

        elif ttype in Token.Name:
            nw = _next_nws(idx)
            pw = _prev_nws(idx)
            if nw == "(":
                color = pal.function
            elif pw == ".":
                color = pal.variable
            elif ttext.isupper() and len(ttext) > 1 and not ttext.isdigit():
                color = pal.other_constant
            else:
                color = pal.variable

        else:
            color = pygments_color(ttype, pal)

        flat.append((color, ttext))

    return split_lines(flat)


def split_lines(flat: List[Tuple[RGB, str]]) -> List[Line]:
    """Break a flat run of (colour, text) at newlines, one list per line."""
    per_line: List[Line] = [[]]
    for color, ttext in flat:
        parts = ttext.split("\n")
        for k, part in enumerate(parts):
            if k > 0:
                per_line.append([])
            if part:
                per_line[-1].append((color, part))
    return per_line
