"""Command line: `cvr render`, `cvr themes`, and `cvr` alone for the GUI."""

import argparse
import logging
import sys
from typing import List, Optional, Tuple

from . import __version__
from .palette import DEFAULT_PALETTE
from .renderer import ANIMATION_STYLES, RenderError, render
from .themes import default_theme, list_themes


def _resolution(text: str) -> Tuple[int, int]:
    try:
        w, h = (int(v) for v in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT, got {text!r}")
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("width and height must be positive")
    return w, h


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cvr",
        description="Turn source code into a typewriter video in your VS Code "
                    "theme. Run with no command to open the app.")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("gui", help="open the app (the default)")

    r = sub.add_parser("render", help="render a source file to a video",
                       description="Render a source file to a typewriter video.")
    r.add_argument("file", help="source file to type out")
    r.add_argument("-o", "--output", metavar="PATH",
                   help="video to write (default: FILE_typewriter.mp4 here)")
    r.add_argument("-t", "--theme", metavar="NAME",
                   help="installed VS Code theme or a theme json path "
                        "(default: the theme VS Code is set to)")
    r.add_argument("-d", "--duration", type=float, metavar="SECONDS",
                   help="fixed length in seconds; without it the delays decide")
    r.add_argument("-r", "--resolution", type=_resolution, default=(1280, 720),
                   metavar="WxH", help="frame size (default: 1280x720)")
    r.add_argument("--fps", type=int, default=30,
                   help="frames per second (default: 30)")
    r.add_argument("--font", metavar="PATH", help="TrueType font file")
    r.add_argument("--font-size", type=int, default=18, metavar="PX",
                   help="font size in pixels (default: 18)")
    r.add_argument("--style", choices=ANIMATION_STYLES, default="smooth",
                   help="animation style (default: smooth)")
    r.add_argument("--char-delay", type=float, default=0.05, metavar="SECONDS",
                   help="pause after each character (default: 0.05)")
    r.add_argument("--line-delay", type=float, default=0.30, metavar="SECONDS",
                   help="pause after each line (default: 0.30)")
    r.add_argument("--language", metavar="NAME",
                   help="Pygments lexer name, when the file extension is not enough")
    r.add_argument("--no-line-numbers", action="store_true",
                   help="hide the line number gutter")
    r.add_argument("--no-tab", action="store_true",
                   help="hide the file name tab")
    r.add_argument("--no-cursor", action="store_true", help="hide the cursor")
    r.add_argument("--no-blink", action="store_true",
                   help="keep the cursor solid")
    r.add_argument("--no-follow", action="store_true",
                   help="do not scroll to follow the cursor")
    r.add_argument("--no-smooth-scroll", action="store_true",
                   help="jump instead of gliding when scrolling")
    r.add_argument("--no-highlight", action="store_true",
                   help="plain text, no syntax colours")
    r.add_argument("--type-indent", action="store_true",
                   help="type leading spaces instead of indenting instantly")
    r.add_argument("--compatible", action="store_true",
                   help="4:2:0 video that Windows Media Player can open "
                        "(about one level off on dark tones)")
    r.add_argument("--open", action="store_true",
                   help="open the video when it is done")
    r.add_argument("-q", "--quiet", action="store_true",
                   help="no progress bar")
    r.add_argument("-v", "--verbose", action="store_true",
                   help="print timing details")

    t = sub.add_parser("themes", help="list installed VS Code themes",
                       description="List the colour themes VS Code has "
                                   "installed. * marks the default.")
    t.add_argument("filter", nargs="?", default="",
                   help="only show themes whose name contains this")
    t.add_argument("--paths", action="store_true",
                   help="show each theme's json file")
    return parser


def _progress_printer(stream):
    def show(pct: float, _total: float, status: str, eta: float):
        width  = 28
        filled = int(width * pct / 100)
        bar    = "#" * filled + "-" * (width - filled)
        eta_s  = f"  ETA {eta:.0f}s" if eta > 0 else ""
        stream.write(f"\r[{bar}] {pct:5.1f}%  {status}{eta_s}".ljust(79))
        stream.flush()
    return show


def _cmd_render(args) -> int:
    show_progress = not args.quiet and sys.stderr.isatty()
    try:
        path = render(
            args.file, args.output,
            theme             = args.theme,
            duration          = args.duration,
            language          = args.language,
            progress          = _progress_printer(sys.stderr) if show_progress else None,
            resolution        = args.resolution,
            fps               = args.fps,
            font_path         = args.font,
            font_size         = args.font_size,
            animation_type    = args.style,
            char_delay        = args.char_delay,
            line_delay        = args.line_delay,
            show_line_numbers = not args.no_line_numbers,
            show_filename_bar = not args.no_tab,
            show_cursor       = not args.no_cursor,
            cursor_blink      = not args.no_blink,
            follow_cursor     = not args.no_follow,
            smooth_scroll     = not args.no_smooth_scroll,
            syntax_highlighting = not args.no_highlight,
            instant_indent    = not args.type_indent,
            exact_colour      = not args.compatible,
        )
    except KeyboardInterrupt:
        if show_progress:
            sys.stderr.write("\n")
        print("cvr: cancelled", file=sys.stderr)
        return 130
    except (RenderError, LookupError, OSError, ValueError) as exc:
        if show_progress:
            sys.stderr.write("\n")
        print(f"cvr: {exc}", file=sys.stderr)
        return 1

    if show_progress:
        sys.stderr.write("\n")
    print(path)
    if args.open:
        from .players import Player
        try:
            Player("System default", "default").launch(path)
        except OSError as exc:
            print(f"cvr: could not open the video: {exc}", file=sys.stderr)
    return 0


def _cmd_themes(args) -> int:
    themes  = list_themes()
    default = default_theme(themes)
    wanted  = args.filter.lower()
    rows = [(t.label, t.path) for t in themes] + [(DEFAULT_PALETTE.name, "(built in)")]
    for label, path in rows:
        if wanted and wanted not in label.lower():
            continue
        mark = "*" if default and label == default.label else " "
        print(f"{mark} {label}" + (f"\n      {path}" if args.paths else ""))
    if not themes:
        print("No VS Code themes found; only the built-in palette is available.",
              file=sys.stderr)
    return 0


def _cmd_gui(_args) -> int:
    try:
        from .gui import main as gui_main
    except ImportError as exc:
        print(f"cvr: the app needs Tkinter, which this Python lacks ({exc}).\n"
              "On Debian or Ubuntu: sudo apt install python3-tk", file=sys.stderr)
        return 1
    gui_main()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        format="cvr: %(message)s",
        level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING)
    command = args.command or "gui"
    return {"render": _cmd_render, "themes": _cmd_themes,
            "gui": _cmd_gui}[command](args)
