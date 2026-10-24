import os
import subprocess
import threading

import numpy as np
import pytest

import cvr
from cvr.encoders import ffmpeg_exe
from cvr.renderer import (FINAL_PAUSE_SECONDS, Renderer, count_typed_chars,
                          solve_delays)

needs_ffmpeg = pytest.mark.skipif(ffmpeg_exe() is None, reason="no ffmpeg")


def test_solve_delays_hits_the_target():
    chars, lines = 900, 40
    cd, ld = solve_delays(chars, lines, 0.05, 0.30, 30.0)
    assert chars * cd + lines * ld + FINAL_PAUSE_SECONDS == pytest.approx(30.0)
    assert ld / cd == pytest.approx(6.0)


def test_solve_delays_leaves_impossible_targets_alone():
    assert solve_delays(100, 5, 0.05, 0.3, 1.0) == (0.05, 0.3)
    assert solve_delays(0, 0, 0.05, 0.3, 30.0) == (0.05, 0.3)


def test_count_typed_chars_skips_indentation():
    lines = ["def f():", "    return 1"]
    assert count_typed_chars(lines, instant_indent=True) == 8 + 8
    assert count_typed_chars(lines, instant_indent=False) == 8 + 12


def test_renderer_needs_code():
    with pytest.raises(ValueError):
        Renderer()
    with pytest.raises(ValueError):
        Renderer(source="x = 1", animation_type="wobbly")


def test_source_string_without_a_file(small):
    r = Renderer(source="x = 1\ny = 2\n", language="python", **small)
    assert r.display_name == "untitled"
    assert r.wrapped_lines == ["x = 1", "y = 2"]
    assert r.output_path.endswith("untitled_typewriter.mp4")


def test_frames_match_the_requested_duration(sample_file):
    # Tall enough for the whole sample, so no font scrolls it out of view.
    r = Renderer(sample_file, target_duration=6, resolution=(480, 360),
                 fps=10, font_size=12)
    frames = list(r.frames())
    assert abs(len(frames) - r.total_frames()) <= 1
    assert len(frames) / r.fps == pytest.approx(6, abs=0.2)
    first, last = frames[0], frames[-1]
    assert first.shape == (360, 480, 3) and first.dtype == np.uint8
    # In the text area, the finished frame has far more ink than the first,
    # which holds one typed character and the cursor.
    bg = np.array(r.palette.bg, dtype=np.uint8)
    text_area = lambda f: f[r._text_y_start:, r._text_x_start:r.resolution[0] - r.padding]
    inked = lambda f: int((text_area(f) != bg).any(axis=2).sum())
    assert inked(last) > inked(first) * 5


def test_long_lines_wrap_to_the_frame(small):
    r = Renderer(source="x = '" + "a" * 300 + "'\n", **small)
    assert len(r.wrapped_lines) > 1
    assert r.line_numbers[0] == 1 and r.line_numbers[1] is None


def test_cancel_removes_the_partial_file(sample_file, small, tmp_path):
    out = str(tmp_path / "cancelled.mp4")
    cancel = threading.Event()
    cancel.set()
    r = Renderer(sample_file, out, **small)
    assert r.render(cancel_flag=cancel) is False
    assert r.error == "cancelled"
    assert not os.path.exists(out)
    with pytest.raises(cvr.RenderCancelled):
        cvr.render(sample_file, out, theme=cvr.DEFAULT_PALETTE, cancel=cancel, **small)


def test_render_error_is_raised(sample_file, small, tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("not a folder")
    with pytest.raises(cvr.RenderError):
        cvr.render(sample_file, str(blocker / "out.mp4"),
                   theme=cvr.DEFAULT_PALETTE, **small)


@needs_ffmpeg
def test_render_writes_a_video_with_exact_colours(sample_file, small, tmp_path):
    out = str(tmp_path / "circle.mp4")
    calls = []
    pal = cvr.Palette("test", bg="#1F1B2E")
    path = cvr.render(sample_file, out, theme=pal, duration=3,
                      progress=lambda *a: calls.append(a), **small)
    assert path == out and os.path.getsize(out) > 0
    assert calls[-1][0] == 100 and calls[-1][2].startswith("Done")

    # Decode the last frame: flat background must survive encoding untouched.
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-sseof", "-0.2", "-i", out,
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    frame = np.frombuffer(raw, np.uint8)[-320 * 180 * 3:].reshape(180, 320, 3)
    assert tuple(frame[-5, -40]) == pal.bg


def test_opencv_fallback_keeps_channel_order(sample_file, small, tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    from cvr import encoders
    monkeypatch.setattr(encoders.FFmpegWriter, "open", classmethod(lambda cls, *a, **k: None))
    out = str(tmp_path / "opencv.mp4")
    pal = cvr.Palette("test", bg="#B03020")             # strongly red, not blue
    cvr.render(sample_file, out, theme=pal, duration=3, **small)

    cap = cv2.VideoCapture(out)
    ok, bgr = cap.read()
    cap.release()
    assert ok
    b, g, r = (int(v) for v in bgr[-5, -40])
    assert abs(r - 0xB0) <= 6 and abs(g - 0x30) <= 6 and abs(b - 0x20) <= 6


def test_line_numbers_appear_as_lines_are_typed(sample_file, small):
    r = Renderer(sample_file, show_filename_bar=False, **small)
    bg = np.array(r.palette.bg, dtype=np.uint8)

    def gutter_inked(frame, row):
        y = r._text_y_start + row * r.line_height_px
        cell = frame[y:y + r.line_height_px, r.padding:r.padding + r._gutter_width]
        return bool((cell != bg).any())

    typing_line_2 = r.create_frame(2, 0)
    assert all(gutter_inked(typing_line_2, row) for row in (0, 1, 2))
    assert not any(gutter_inked(typing_line_2, row) for row in (3, 4, 5))
    finished = r.create_frame(len(r.wrapped_lines), 0)
    assert gutter_inked(finished, 5)
