from cvr.highlight import get_lexer, tokenize
from cvr.palette import DEFAULT_PALETTE as PAL

from conftest import SAMPLE


def colour_of(lines, text):
    for line in lines:
        for colour, run in line:
            if run == text:
                return colour
    raise AssertionError(f"{text!r} not found")


def test_python_tokens_get_vscode_roles():
    lines = tokenize(SAMPLE, PAL, filename="circle.py")
    assert colour_of(lines, "import") == PAL.keyword_ctrl
    assert colour_of(lines, "def") == PAL.storage
    assert colour_of(lines, "area") == PAL.function
    assert colour_of(lines, "float") == PAL.type_
    assert colour_of(lines, "return") == PAL.keyword_ctrl
    assert colour_of(lines, "# pi r squared") == PAL.comment
    assert colour_of(lines, "2") == PAL.number
    assert colour_of(lines, "print") == PAL.builtin_fn


def test_one_token_list_per_source_line():
    lines = tokenize(SAMPLE, PAL, filename="circle.py")
    for src_line, toks in zip(SAMPLE.split("\n"), lines):
        assert "".join(t for _, t in toks) == src_line


def test_leading_blank_lines_do_not_shift_tokens():
    source = "\n\nimport os\nx = 1\n"
    lines = tokenize(source, PAL, filename="x.py")
    assert lines[0] == [] and lines[1] == []
    assert lines[2][0] == (PAL.keyword_ctrl, "import")


def test_language_name_beats_file_extension():
    assert get_lexer("notes.txt", language="python").name == "Python"
    assert get_lexer("main.rs").name == "Rust"
