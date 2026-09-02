"""
Auditing a manuscript — SPEC §13.3.

Reads a .docx with Zotero fields and says what is wrong with it. It writes
nothing anywhere, and it needs neither network nor credentials: everything it
checks can be worked out from the file. That is why it is catena's first piece —
it is already worth something on its own, it runs on documents written by other
people, and it is how catena will check itself once the rest exists.

The check that matters most is the duplicate surrogate. When a URI does not
resolve, Zotero builds a distinct surrogate item (verified in Word, SPEC §12.2
case 5): the same paper reached through two different URIs takes two numbers and
two bibliography entries. In Vancouver that is one stray `(4)` nobody notices;
in APA it becomes a `2008a`/`2008b` that looks like a second paper by the same
author.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .fields import (
    Citation,
    has_bibliography,
    looks_like_doi,
    parse_citations,
    parse_prefs,
)
from .ooxml import Document

# Severity: error = the resulting bibliography is wrong; warning = fragile or
# not portable; note = worth knowing, not worth fixing.
ERROR, WARNING, NOTE = "error", "warning", "note"

ORDER = {ERROR: 0, WARNING: 1, NOTE: 2}


@dataclass
class Finding:
    level: str
    code: str
    message: str
    detail: str = ""

    def __str__(self) -> str:
        s = f"[{self.level}] {self.code}: {self.message}"
        return s + (f"\n    {self.detail}" if self.detail else "")


@dataclass
class Report:
    path: Path
    counts: dict[str, int]
    findings: list[Finding]
    style: str | None
    citations: list[Citation]

    @property
    def clean(self) -> bool:
        return not any(f.level == ERROR for f in self.findings)

    def by_level(self, level: str) -> list[Finding]:
        return [f for f in self.findings if f.level == level]


def audit(path: str | Path) -> Report:
    doc = Document.open(path)
    cits = parse_citations(doc)
    prefs = parse_prefs(doc)
    found: list[Finding] = []

    if not cits:
        found.append(
            Finding(
                NOTE,
                "no-fields",
                "the document has no Zotero fields",
                "every citation in it, if any, is hand-typed text: that is the "
                "case in SPEC §13.1, not a defect",
            )
        )

    _check_surrogate_duplicates(cits, found)
    _check_uris(cits, found)
    _check_metadata(cits, found)
    _check_citation_ids(cits, found)
    _check_note_styles(cits, found)
    _check_document_level(doc, cits, prefs, found)

    found.sort(key=lambda f: (ORDER[f.level], f.code))
    return Report(
        path=Path(path),
        counts=doc.counts(),
        findings=found,
        style=prefs.style_name if prefs else None,
        citations=cits,
    )


# --- checks ------------------------------------------------------------------


def _check_surrogate_duplicates(cits: list[Citation], out: list[Finding]) -> None:
    """Same paper under different URIs: two bibliography entries (SPEC §3.2)."""
    by_sig: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, str] = {}
    for c in cits:
        for it in c.items:
            sig = it.signature()
            by_sig[sig].add("|".join(sorted(it.uris)) or "<no uri>")
            labels.setdefault(
                sig, f"{it.first_author or '?'} {it.year or '?'} — {it.title[:60]}"
            )
    for sig, uris in by_sig.items():
        if len(uris) > 1:
            out.append(
                Finding(
                    ERROR,
                    "duplicate-surrogate",
                    f"the same paper appears under {len(uris)} different URIs: {labels[sig]}",
                    "this produces two bibliography entries and two numbers; in "
                    "APA it becomes a year disambiguation (2008a/2008b) that does "
                    "not exist.\n    " + "\n    ".join(sorted(uris)),
                )
            )


def _check_uris(cits: list[Citation], out: list[Finding]) -> None:
    local: list[str] = []
    missing: list[str] = []
    for c in cits:
        for it in c.items:
            if not it.uris:
                missing.append(f"citation #{c.order + 1}")
            elif it.is_local_uri:
                local.append(it.library or "?")
    if missing:
        out.append(
            Finding(
                ERROR,
                "missing-uri",
                f"{len(missing)} items have no URI",
                "Zotero resolves by URI; without one it falls back to the local "
                "numeric id and can relink to the wrong item (SPEC §7.7)",
            )
        )
    if local:
        profiles = sorted(set(local))
        out.append(
            Finding(
                WARNING,
                "local-uri",
                f"{len(local)} items point at a local Zotero profile",
                "these URIs resolve on exactly one machine in the world: to "
                "co-authors the items are orphans and only format from the "
                f"embedded data (SPEC §7.2). Profiles: {', '.join(profiles)}",
            )
        )


def _check_metadata(cits: list[Citation], out: list[Finding]) -> None:
    bad_doi: list[str] = []
    no_title: list[str] = []
    no_data: list[str] = []
    for c in cits:
        for it in c.items:
            if not it.item_data:
                no_data.append(f"#{c.order + 1}")
                continue
            raw = it.item_data.get("DOI")
            if raw and not looks_like_doi(raw):
                bad_doi.append(f"{it.first_author or '?'} {it.year or '?'}: DOI={raw!r}")
            if not it.title:
                no_title.append(f"#{c.order + 1}")
    if no_data:
        out.append(
            Finding(
                ERROR,
                "missing-itemdata",
                f"{len(no_data)} items carry no embedded data",
                "if the URI fails to resolve there is nothing to fall back on: "
                "Zotero opens a reselect dialog or raises an error",
            )
        )
    if bad_doi:
        out.append(
            Finding(
                WARNING,
                "malformed-doi",
                f"{len(bad_doi)} items have a DOI that is not a DOI",
                "\n    ".join(bad_doi[:10]),
            )
        )
    if no_title:
        out.append(
            Finding(WARNING, "missing-title", f"{len(no_title)} items have no title")
        )


def _check_citation_ids(cits: list[Citation], out: list[Finding]) -> None:
    ids = [c.citation_id for c in cits if c.citation_id]
    dupes = [k for k, v in Counter(ids).items() if v > 1]
    if dupes:
        out.append(
            Finding(
                WARNING,
                "duplicate-citationid",
                f"{len(dupes)} citationIDs appear more than once",
                "they must be unique per occurrence, not per item (SPEC §7.1): "
                f"{', '.join(dupes[:8])}",
            )
        )


def _check_note_styles(cits: list[Citation], out: list[Finding]) -> None:
    notes = [c for c in cits if c.is_footnote_style]
    if notes:
        out.append(
            Finding(
                NOTE,
                "note-based-style",
                f"{len(notes)} citations have a non-zero noteIndex",
                "the document uses a footnote style: catena does not handle "
                "those yet and refuses them explicitly (SPEC §7.6)",
            )
        )


def _check_document_level(doc, cits, prefs, out: list[Finding]) -> None:
    if cits and not prefs:
        out.append(
            Finding(
                ERROR,
                "missing-prefs",
                "there are Zotero fields but no ZOTERO_PREF property",
                "without the document preferences Zotero does not know which "
                "style to format with, and changing style from Word does not "
                "work (SPEC §7.4)",
            )
        )
    if prefs and prefs.field_type and prefs.field_type != "Field":
        out.append(
            Finding(
                WARNING,
                "field-type",
                f"fieldType is {prefs.field_type!r}, expected 'Field'",
                "the injector only works on real Word fields",
            )
        )
    if cits and not has_bibliography(doc):
        out.append(
            Finding(
                WARNING,
                "missing-bibliography",
                "there are citations but no bibliography field",
                "deliberate in an abstract or a letter; suspicious in a manuscript",
            )
        )
    authors = doc.revision_authors
    if authors:
        out.append(
            Finding(
                NOTE,
                "tracked-changes",
                f"the document carries tracked changes from {len(authors)} authors",
                "an injection has to preserve the revision markup and its "
                f"attribution (SPEC §8.3): {', '.join(authors)}",
            )
        )


# --- rendering ---------------------------------------------------------------


def render(report: Report) -> str:
    c = report.counts
    lines = [
        f"{report.path.name}",
        "",
        f"  paragraphs {c['paragraphs']}   characters {c['characters']}   "
        f"Zotero fields {c['zotero_fields']}   bibliography {c['bibliography_fields']}",
        f"  revisions +{c['insertions']} -{c['deletions']}   "
        f"comments {c['comments']}   footnotes {c['footnotes']}",
    ]
    if report.style:
        lines.append(f"  style {report.style}")
    lines.append("")

    if not report.findings:
        lines.append("  nothing to report.")
        return "\n".join(lines)

    for level in (ERROR, WARNING, NOTE):
        for f in report.by_level(level):
            lines.append("  " + str(f).replace("\n", "\n  "))
            lines.append("")

    n_err = len(report.by_level(ERROR))
    n_warn = len(report.by_level(WARNING))
    lines.append(
        f"  {n_err} errors, {n_warn} warnings, {len(report.by_level(NOTE))} notes"
    )
    return "\n".join(lines)
