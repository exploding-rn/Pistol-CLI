"""Shared terminal presentation. Importing or displaying it writes no state."""
from __future__ import annotations

import os
import shutil
import sys

LOGO = (
    "██████╗ ██╗███████╗████████╗ ██████╗ ██╗",
    "██╔══██╗██║██╔════╝╚══██╔══╝██╔═══██╗██║",
    "██████╔╝██║███████╗   ██║   ██║   ██║██║",
    "██╔═══╝ ██║╚════██║   ██║   ██║   ██║██║",
    "██║     ██║███████║   ██║   ╚██████╔╝███████╗",
    "╚═╝     ╚═╝╚══════╝   ╚═╝    ╚═════╝ ╚══════╝",
)


def _ansi(stream) -> bool:
    if not stream.isatty() or os.environ.get("NO_COLOR") is not None or os.environ.get("TERM") == "dumb":
        return False
    if os.name == "nt":
        import ctypes
        import msvcrt
        try:
            handle = msvcrt.get_osfhandle(stream.fileno())
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            mode = ctypes.c_ulong()
            if not kernel.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)):
                return False
            return bool(kernel.SetConsoleMode(ctypes.c_void_p(handle), mode.value | 4))
        except (OSError, ValueError):
            return False
    return True


class Presentation:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.interactive = self.stream.isatty()
        self.ansi = _ansi(self.stream)
        self.width = max(10, shutil.get_terminal_size((80, 24)).columns - 1)

    def banner(self):
        encoding = self.stream.encoding or "utf-8"
        try:
            "".join(LOGO).encode(encoding)
            lines = LOGO
        except (UnicodeError, LookupError):
            lines = ("PISTOL",)
        truecolor = os.environ.get("COLORTERM") in {"truecolor", "24bit"} or bool(os.environ.get("WT_SESSION"))
        for index, line in enumerate(lines):
            if self.ansi:
                t = index / max(1, len(lines) - 1)
                if truecolor:
                    start, end, fraction = ((60, 190, 110), (60, 205, 205), t * 2) if t <= .5 else ((60, 205, 205), (90, 170, 255), (t - .5) * 2)
                    rgb = [int(a + (b - a) * fraction) for a, b in zip(start, end)]
                    color = "38;2;" + ";".join(map(str, rgb))
                else:
                    color = "32" if t < .34 else "36" if t < .67 else "94"
                line = f"\x1b[{color}m{line[:self.width]}\x1b[0m"
            print(line, file=self.stream)

    def progress(self, percent: int, label: str = "", *, final=False):
        percent = max(0, min(100, percent))
        if not self.interactive and not final:
            return
        width = min(32, max(4, self.width - len(label) - 10))
        filled = width * percent // 100
        line = f"[{'#' * filled}{'-' * (width - filled)}] {percent}% {label}"[:self.width]
        if self.interactive:
            print("\r" + line.ljust(self.width), end="\n" if final else "", file=self.stream, flush=True)
        else:
            print(line, file=self.stream)


def initialize(stream=None):
    presentation = Presentation(stream)
    presentation.banner()
    presentation.progress(0)
    presentation.progress(100, final=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--banner", action="store_true")
    parser.add_argument("--percent", type=int)
    parser.add_argument("--label", default="")
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    presentation = Presentation()
    if args.banner:
        presentation.banner()
    elif args.percent is not None:
        presentation.progress(args.percent, args.label, final=args.complete)
    else:
        initialize()
