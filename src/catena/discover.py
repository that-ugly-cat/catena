"""
Reading the citations an author typed, in whatever notation they typed them.

`inject.py` places markers we chose. This finds the ones we did not: `(Rosato
et al., 2008)`, `(Bullo & Hearn, 2024; Hudson, 2021)`, and the hand-made
reference list at the end that usually carries the DOI.

Every rule here comes from one real draft (SPEC §14), and each of them exists
because the obvious version was wrong on it:

- the reference list heading was `REFERENCES (NOT FINALISED YET!)`, so an exact
  match finds nothing and a heuristic fallback lands mid-list, losing every
  entry from A to B;
- 29 of its 90 entries were hand-numbered `1. `, `8. `, `87. ` and the rest were
  not, so the surname is not at the start of the string and matching fails
  silently — that is how a first attempt reported a confident, wrong 83%;
- APA and Vancouver entries sat in the same list;
- 27 parenthetical groups held only digits: issue numbers like `334(1)`, years
  without an author;
- some citations carry prose inside the bracket, `(borrowing from Hoffmann &
  Tarzian, 2001)`;
- the same reference appeared as `Becker et al., 2022` and `Becker et al. 2022`;
- surnames are not ASCII (`Grundström`).

Nothing here resolves anything. It reports candidates and what it could attach
to them, and says plainly what it could not: a citation this cannot read is a
citation a person looks at, never one catena guesses.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .ooxml import Document

# A parenthetical group that might be a citation: it has a year in it, and at
# least three letters somewhere. The letter test alone removes all 27 of the
# issue-number false positives in the calibration draft.
RE_PAREN = re.compile(r"\(([^()]{0,200}?\b(?:19|20)\d{2}[a-z]?[^()]{0,40})\)")
RE_HAS_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
RE_YEAR = re.compile(r"\b((?:19|20)\d{2})([a-z])?\b")
# A surname: starts uppercase, may carry accents, apostrophes or hyphens.
RE_SURNAME = re.compile(r"([^\W\d_][\w'’-]+)", re.UNICODE)

RE_DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)
RE_URL = re.compile(r"https?://[^\s)\]<>]+")
RE_ISBN = re.compile(r"\bISBN[:\s]*((?:97[89][ -]?)?\d[\d\s-]{8,}[\dXx])")

RE_LIST_HEADING = re.compile(
    r"^\s*(references|bibliography|reference list|works cited|bibliografia|"
    r"riferimenti)\b", re.IGNORECASE
)
# An entry of a hand-made list: optional manual number, then a surname, then
# something that looks like initials or a year.
RE_ENTRY = re.compile(
    r"^(?:\d{1,3}[.)]\s*)?[^\W\d_][\w'’-]+[,.]\s", re.UNICODE
)
RE_NUMBER_PREFIX = re.compile(r"^\d{1,3}[.)]\s*")

TRAILING_PUNCT = ".,;:"


def _fold(text: str) -> str:
    """Accent-insensitive, case-insensitive, punctuation-free comparison key."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9]+", "", stripped.lower())


@dataclass
class Reference:
    """One entry of the author's own reference list."""

    raw: str
    surname: str | None
    year: str | None
    doi: str | None = None
    url: str | None = None
    isbn: str | None = None

    @property
    def identifier(self) -> tuple[str, str] | None:
        """The best handle for ingest, in the order SPEC §5 resolves them."""
        if self.doi:
            return ("doi", self.doi)
        if self.isbn:
            return ("isbn", self.isbn)
        if self.url:
            return ("url", self.url)
        return None


@dataclass
class Candidate:
    """One in-text citation, as the author wrote it."""

    raw: str
    paragraph: int
    atoms: list[str] = field(default_factory=list)
    grouped: bool = False
    matches: list[Reference | None] = field(default_factory=list)

    @property
    def resolvable(self) -> bool:
        return bool(self.matches) and all(
            m is not None and m.identifier for m in self.matches
        )


@dataclass
class Discovery:
    path: Path
    body_paragraphs: int
    candidates: list[Candidate]
    references: list[Reference]
    list_starts_at: int | None

    def summary(self) -> dict:
        atoms = [a for c in self.candidates for a in c.atoms]
        distinct = {_fold(a) for a in atoms}
        auto = sum(
            1 for c in self.candidates for m in c.matches if m and m.identifier
        )
        needs_human = sum(
            1 for c in self.candidates for m in c.matches if m and not m.identifier
        )
        unmatched = sum(1 for c in self.candidates for m in c.matches if m is None)
        return {
            "paragraphs_of_body": self.body_paragraphs,
            "citation_parentheses": len(self.candidates),
            "grouped_parentheses": sum(1 for c in self.candidates if c.grouped),
            "atomic_citations": len(atoms),
            "distinct_citations": len(distinct),
            "reference_entries": len(self.references),
            "entries_with_doi": sum(1 for r in self.references if r.doi),
            "entries_with_url_only": sum(1 for r in self.references if r.url and not r.doi),
            "entries_without_identifier": sum(
                1 for r in self.references if not r.identifier
            ),
            "resolvable_automatically": auto,
            "needs_a_human": needs_human,
            "matched_to_nothing": unmatched,
        }


# --- splitting the document ---------------------------------------------------


def _find_list_start(paras: list[str]) -> int | None:
    """Where the author's reference list begins.

    The heading first, matched as a prefix and not as an equality — the real one
    was `REFERENCES (NOT FINALISED YET!)`. If there is no heading at all, the
    first sustained run of entry-shaped paragraphs, which is what a list looks
    like even when nobody labelled it.
    """
    for i, p in enumerate(paras):
        if RE_LIST_HEADING.match(p) and len(p) < 80:
            for j in range(i + 1, min(i + 5, len(paras))):
                if paras[j].strip():
                    return j
            return i + 1

    window = 20
    for i in range(len(paras) - 3):
        chunk = [p for p in paras[i : i + window] if p.strip()]
        if len(chunk) >= 8 and sum(bool(RE_ENTRY.match(p)) for p in chunk) >= len(chunk) * 0.75:
            return i
    return None


def _parse_reference(raw: str) -> Reference:
    body = RE_NUMBER_PREFIX.sub("", raw).strip()
    m = RE_SURNAME.match(body)
    year = RE_YEAR.search(body)
    doi = RE_DOI.search(body)
    url = RE_URL.search(body)
    isbn = RE_ISBN.search(body)
    return Reference(
        raw=body,
        surname=m.group(1) if m else None,
        year=year.group(1) if year else None,
        doi=doi.group(0).rstrip(TRAILING_PUNCT) if doi else None,
        url=url.group(0).rstrip(TRAILING_PUNCT) if url else None,
        isbn=isbn.group(1).replace(" ", "").replace("-", "") if isbn else None,
    )


def _atoms(inside: str) -> list[str]:
    """Split a parenthetical group into single citations."""
    out = []
    for part in re.split(r";\s*", inside):
        part = part.strip()
        if RE_YEAR.search(part) and RE_HAS_WORD.search(part):
            out.append(part)
    return out


def _key(atom: str) -> tuple[str, str] | None:
    """(folded surname, year) — the handle an in-text citation offers.

    The surname is taken from the first capitalised word, so prose inside the
    bracket does not defeat it: `borrowing from Hoffmann & Tarzian, 2001` still
    yields Hoffmann.
    """
    year = RE_YEAR.search(atom)
    if not year:
        return None
    for word in re.findall(r"[^\W\d_][\w'’-]+", atom, re.UNICODE):
        if word[:1].isupper() and word.lower() not in {
            "et", "al", "and", "see", "cf", "in", "the", "borrowing", "from", "eg", "ie",
        }:
            return (_fold(word), year.group(1))
    return None


def discover(path: str | Path) -> Discovery:
    doc = Document.open(path)
    paras = [p.text.strip() for p in doc.paragraphs]

    start = _find_list_start(paras)
    body = paras[:start] if start is not None else paras
    tail = [p for p in paras[start:] if p.strip()] if start is not None else []

    references = [_parse_reference(p) for p in tail]
    index: dict[tuple[str, str], Reference] = {}
    for r in references:
        if r.surname and r.year:
            index.setdefault((_fold(r.surname), r.year), r)

    candidates: list[Candidate] = []
    for n, p in enumerate(body):
        for m in RE_PAREN.finditer(p):
            inside = m.group(1)
            if not RE_HAS_WORD.search(inside):
                continue
            atoms = _atoms(inside)
            if not atoms:
                continue
            cand = Candidate(
                raw=m.group(0), paragraph=n, atoms=atoms, grouped=len(atoms) > 1
            )
            for a in atoms:
                k = _key(a)
                cand.matches.append(index.get(k) if k else None)
            candidates.append(cand)

    return Discovery(
        path=Path(path),
        body_paragraphs=len([p for p in body if p]),
        candidates=candidates,
        references=references,
        list_starts_at=start,
    )


def render(d: Discovery) -> str:
    s = d.summary()
    lines = [f"{d.path.name}", ""]
    for k, v in s.items():
        lines.append(f"  {k.replace('_', ' '):32} {v}")
    lines.append("")

    unresolved = [
        (c, a)
        for c in d.candidates
        for a, m in zip(c.atoms, c.matches)
        if m is None
    ]
    if unresolved:
        lines.append(f"  {len(unresolved)} citations matched to no entry:")
        seen = set()
        for c, a in unresolved:
            if a in seen:
                continue
            seen.add(a)
            lines.append(f"    - {a}   (paragraph {c.paragraph})")
        lines.append("")

    no_id = [r for r in d.references if not r.identifier]
    if no_id:
        lines.append(
            f"  {len(no_id)} reference entries carry no DOI, ISBN or URL — "
            "these resolve by title, which is never accepted automatically:"
        )
        for r in no_id[:8]:
            lines.append(f"    - {r.raw[:88]}")
    return "\n".join(lines)
