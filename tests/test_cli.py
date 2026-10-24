import os

import pytest

import cvr
from cvr import cli, themes
from cvr.encoders import ffmpeg_exe


@pytest.mark.skipif(ffmpeg_exe() is None, reason="no ffmpeg")
def test_render_command(sample_file, tmp_path, capsys):
    out = str(tmp_path / "out.mp4")
    code = cli.main(["render", sample_file, "-o", out, "-t", "Bundled Dark Plus",
                     "-r", "320x180", "--fps", "10", "-d", "3", "--no-tab", "-q"])
    assert code == 0
    assert os.path.getsize(out) > 0
    assert capsys.readouterr().out.strip() == out


def test_unknown_theme_is_reported(sample_file, monkeypatch, capsys):
    monkeypatch.setattr(themes, "list_themes", lambda: [])
    assert cli.main(["render", sample_file, "-t", "No Such Theme", "-q"]) == 1
    assert "No Such Theme" in capsys.readouterr().err


def test_bad_resolution_is_a_usage_error(sample_file):
    with pytest.raises(SystemExit) as exc:
        cli.main(["render", sample_file, "-r", "wide"])
    assert exc.value.code == 2


def test_themes_command_lists_the_bundled_palette(capsys):
    assert cli.main(["themes", "bundled"]) == 0
    assert cvr.DEFAULT_PALETTE.name in capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert cvr.__version__ in capsys.readouterr().out
