"""catena's command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .audit import audit, render
from .discover import discover
from .discover import render as render_discovery
from .inject import inject


def _utf8_console() -> None:
    """The Windows console is cp1252 and eats the SPEC references (§)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _cmd_audit(args) -> int:
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


def _cmd_discover(args) -> int:
    d = discover(args.file)
    if args.json:
        print(
            json.dumps(
                {
                    "summary": d.summary(),
                    "candidates": [
                        {
                            "raw": c.raw,
                            "paragraph": c.paragraph,
                            "atoms": c.atoms,
                            "grouped": c.grouped,
                            "identifiers": [
                                {"atom": a, "kind": m.identifier[0], "value": m.identifier[1]}
                                if m and m.identifier
                                else {"atom": a, "kind": None, "value": None}
                                for a, m in zip(c.atoms, c.matches)
                            ],
                        }
                        for c in d.candidates
                    ],
                },
                indent=1,
                ensure_ascii=False,
            )
        )
    else:
        print(render_discovery(d))
    return 0


def _cmd_inject(args) -> int:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    fields = plan.get("fields") or {}
    if not fields:
        print("the plan has no fields to place", file=sys.stderr)
        return 2
    prefs = [tuple(p) for p in plan.get("prefs") or []] or None

    report = inject(
        args.file,
        fields,
        out=args.out,
        prefs=prefs,
        visible=plan.get("visible") or {},
    )
    print(f"{report.path.name} -> {report.out}")
    print(f"  placed        {len(report.placed)}")
    if report.prefs_written:
        print(f"  ZOTERO_PREF   {report.prefs_written} properties")
    for s in report.skipped:
        print(f"  SKIPPED       {s.marker}: {s.reason}")
    for m in report.unmatched:
        print(f"  NOT FOUND     {m}")
    if report.skipped or report.unmatched:
        print(
            "\n  Nothing was guessed: a marker catena could not place is left "
            "exactly as it was written."
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(
        prog="catena", description="Zotero references inside Word documents."
    )
    parser.add_argument("--version", action="version", version=f"catena {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "audit", help="check the Zotero fields of a .docx (read-only, no network)"
    )
    p.add_argument("file", nargs="+", help="one or more .docx files")
    p.add_argument(
        "--strict", action="store_true", help="exit 1 on warnings too, not only errors"
    )
    p.set_defaults(fn=_cmd_audit)

    p = sub.add_parser(
        "discover",
        help="find the citations an author typed, and what can be resolved (read-only)",
    )
    p.add_argument("file")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(fn=_cmd_discover)

    p = sub.add_parser(
        "inject", help="place field codes into a copy of a .docx, from a plan"
    )
    p.add_argument("file")
    p.add_argument(
        "--plan",
        required=True,
        help='JSON: {"fields": {marker: field_code}, "prefs": [[name, value]], '
        '"visible": {marker: text}}',
    )
    p.add_argument("--out", required=True, help="where to write; never in place")
    p.set_defaults(fn=_cmd_inject)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
