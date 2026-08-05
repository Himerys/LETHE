"""Terminal UI: Kali-flavoured green-on-black rich console, banner, key input."""
from __future__ import annotations

import sys

from rich.console import Console
from rich.theme import Theme

THEME = Theme(
    {
        "ok": "bold bright_green",
        "accent": "bright_green",
        "dim": "green4",
        "muted": "grey58",
        "warn": "bold yellow",
        "err": "bold bright_red",
        "head": "bold bright_green",
        "kali": "bright_green on black",
    }
)

console = Console(theme=THEME, highlight=False)

BANNER = r"""
███████╗ ██████╗  ██████╗ ████████╗██████╗ ██████╗ ██╗███╗   ██╗████████╗
██╔════╝██╔═══██╗██╔═══██╗╚══██╔══╝██╔══██╗██╔══██╗██║████╗  ██║╚══██╔══╝
█████╗  ██║   ██║██║   ██║   ██║   ██████╔╝██████╔╝██║██╔██╗ ██║   ██║
██╔══╝  ██║   ██║██║   ██║   ██║   ██╔═══╝ ██╔══██╗██║██║╚██╗██║   ██║
██║     ╚██████╔╝╚██████╔╝   ██║   ██║     ██║  ██║██║██║ ╚████║   ██║
╚═╝      ╚═════╝  ╚═════╝    ╚═╝   ╚═╝     ╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝   ╚═╝
██████╗ ███████╗ ██████╗██╗███╗   ███╗ █████╗ ████████╗ ██████╗ ██████╗
██╔══██╗██╔════╝██╔════╝██║████╗ ████║██╔══██╗╚══██╔══╝██╔═══██╗██╔══██╗
██║  ██║█████╗  ██║     ██║██╔████╔██║███████║   ██║   ██║   ██║██████╔╝
██║  ██║██╔══╝  ██║     ██║██║╚██╔╝██║██╔══██║   ██║   ██║   ██║██╔══██╗
██████╔╝███████╗╚██████╗██║██║ ╚═╝ ██║██║  ██║   ██║   ╚██████╔╝██║  ██║
╚═════╝ ╚══════╝ ╚═════╝╚═╝╚═╝     ╚═╝╚═╝  ╚═╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝
"""

TAGLINE = "[ mailbox recon // GDPR art. 17 strike kit // response tracking ]"


def banner(version: str) -> None:
    if console.width >= 76:
        console.print(f"[accent]{BANNER}[/accent]", highlight=False)
    else:
        console.print("[head]FOOTPRINT DECIMATOR[/head]")
    console.print(f"  [dim]{TAGLINE}[/dim]  [muted]v{version}[/muted]\n")


def ok(msg: str) -> None:
    console.print(f"[ok][+][/ok] {msg}")


def info(msg: str) -> None:
    console.print(f"[accent][*][/accent] {msg}")


def warn(msg: str) -> None:
    console.print(f"[warn][!][/warn] {msg}")


def err(msg: str) -> None:
    console.print(f"[err][x][/err] {msg}")


def getkey() -> str:
    """Read a single keypress (falls back to line input on non-tty)."""
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if not line:
            return "q"
        return (line.strip()[:1] or "\r").lower()
    try:
        import msvcrt  # Windows

        ch = msvcrt.getwch()
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch.lower()
    except ImportError:
        pass
    import termios
    import tty

    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return (input("> ").strip()[:1] or "\r").lower()
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch == "\x03":
        raise KeyboardInterrupt
    return ch.lower()
