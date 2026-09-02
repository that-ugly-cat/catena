"""catena's command line."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .audit import audit, render


def _utf8_console() -> None:
    """The Windows console is cp1252 and eats the SPEC references (§)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(
        prog="catena",
        description="Zotero references inside Word documents.",
    )
    parser.add_argument("--version", action="version", version=f"catena {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser(
        "audit",
        help="check the Zotero fields of a .docx (read-only, no network)",
    )
    p_audit.add_argument("file", nargs="+", help="one or more .docx files")
    p_audit.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on warnings too, not only on errors",
    )

    args = parser.parse_args(argv)

    if args.command == "audit":
        worst = 0
        for n, path in enumerate(args.file):
            if n:
                print()
            try:
                report = audit(path)
            except (OSError, ValueError) as e:
                print(f"{path}: {e}", file=sys.stderr)
                worst = max(worst, 2)
                continue
            print(render(report))
            if not report.clean:
                worst = max(worst, 1)
            elif args.strict and report.by_level("warning"):
                worst = max(worst, 1)
        return worst

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
