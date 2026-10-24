"""Video writers. Frames go in as RGB uint8 arrays of shape (height, width, 3)."""

import logging
import shutil
import subprocess
from typing import Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)


def ffmpeg_exe() -> Optional[str]:
    """ffmpeg on PATH, else the copy bundled with the imageio-ffmpeg package."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


class OpenCVWriter:
    """Fallback encoder. Goes through YUV, so dark colours shift slightly."""

    def __init__(self, writer, cv2_module):
        self._writer = writer
        self._cv2 = cv2_module
        self.exact_colour = False

    @classmethod
    def open(cls, path: str, fps: int, size: Tuple[int, int]):
        try:
            import cv2
        except ImportError:
            return None
        fourcc_of = getattr(cv2, "VideoWriter_fourcc", None) or cv2.VideoWriter.fourcc
        for fourcc_str in ("avc1", "mp4v"):
            w = cv2.VideoWriter(path, fourcc_of(*fourcc_str), float(fps), size)
            if w.isOpened():
                return cls(w, cv2)
            w.release()
        return None

    def write(self, frame: np.ndarray):
        self._writer.write(self._cv2.cvtColor(frame, self._cv2.COLOR_RGB2BGR))

    def release(self):
        self._writer.release()


class FFmpegWriter:
    """Encoder that preserves colours exactly.

    OpenCV converts every frame to limited range YUV, which moves the
    theme colours. Encoding through ffmpeg keeps them intact, so what the
    palette says is what the player shows.
    """

    def __init__(self, proc, exact_colour: bool):
        self._proc = proc
        self.exact_colour = exact_colour

    @classmethod
    def open(cls, path: str, fps: int, size: Tuple[int, int],
             exact_colour: bool = False):
        exe = ffmpeg_exe()
        if not exe:
            return None

        if exact_colour:
            # RGB in, RGB out: no colour conversion, no chroma subsampling.
            # Exact, but this is H.264 High 4:4:4 Predictive, which Windows
            # Media Player refuses to open. VLC and mpv play it.
            codec = ["-c:v", "libx264rgb", "-crf", "12", "-pix_fmt", "bgr0"]
        else:
            # Ordinary High profile that plays everywhere. Full range keeps
            # the luma untouched, where limited range would compress it into
            # 16..235 and lose a level on the dark tones a code theme uses.
            codec = ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
                     "-color_range", "pc", "-colorspace", "bt709",
                     "-color_primaries", "bt709", "-color_trc", "bt709",
                     "-vf", "scale=in_range=full:out_range=full"]

        cmd = [
            exe, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{size[0]}x{size[1]}", "-r", str(fps),
            "-i", "-",
            "-an", *codec,
            "-preset", "medium",
            "-movflags", "+faststart",
            path,
        ]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE)
        except OSError:
            return None
        return cls(proc, exact_colour)

    def write(self, frame: np.ndarray):
        self._proc.stdin.write(frame.tobytes())

    def release(self):
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self._proc.kill()


def open_writer(path: str, fps: int, size: Tuple[int, int],
                exact_colour: bool = True):
    """ffmpeg if there is one, else OpenCV, else None."""
    writer = FFmpegWriter.open(path, fps, size, exact_colour=exact_colour)
    if writer is None:
        writer = OpenCVWriter.open(path, fps, size)
        if writer is not None:
            log.warning("ffmpeg not found; using the OpenCV writer, which "
                        "shifts dark colours slightly. pip install imageio-ffmpeg "
                        "to fix that.")
    return writer
