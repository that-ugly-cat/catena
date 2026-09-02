"""Riga di comando di catena."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .audit import audit, render


def _utf8_console() -> None:
    """La console di Windows e' cp1252 e mangia i riferimenti alla SPEC (§)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(
        prog="catena",
        description="Riferimenti Zotero dentro documenti Word.",
    )
    parser.add_argument("--version", action="version", version=f"catena {__version__}")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_audit = sub.add_parser(
        "audit",
        help="controlla i campi Zotero di un .docx (sola lettura, nessuna rete)",
    )
    p_audit.add_argument("file", nargs="+", help="uno o piu' .docx")
    p_audit.add_argument(
        "--strict",
        action="store_true",
        help="esce con codice 1 anche in presenza di soli avvisi",
    )

    args = parser.parse_args(argv)

    if args.comando == "audit":
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
            elif args.strict and report.by_level("avviso"):
                worst = max(worst, 1)
        return worst

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
