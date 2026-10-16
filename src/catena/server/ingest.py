"""
Adding items to Zotero, and the three guards that stand in front of it.

Writing to somebody's library is the only thing catena does that a person
cannot undo by closing a file. So each write passes three checks, and each of
them exists because of a specific way this goes wrong.

**Idempotency.** pyzotero sends a fresh `Zotero-Write-Token` on every call, which
covers its own internal retries but not a second call from the caller — and the
caller here is a model that may retry a tool whose answer got lost. The
`UNIQUE (binding_id, identifier)` row in `ingest_events` is the protection,
because it lives at the level where the retry happens (SPEC §9.1). A source with
no identifier at all gets a synthetic one, stable across retries.

**Deduplication.** DOI, then ISBN, then URL, then a fuzzy title match — and on
the fuzzy rung it does not decide. With tens of thousands of records in a
library, the cost of one question is smaller than the cost of one duplicate, and
a duplicate here is not cosmetic: the same paper under two URIs produces two
bibliography entries and, in an author-date style, a 2008a/2008b that does not
exist. The rows already planned as new count too, so one batch cannot add the
same paper twice under two identifiers.

**The author cross-check.** When a draft's citation reads `[Assan, 10.1136/…]`,
the surname is not an identifier — it is a *check*. If the DOI resolves to a
paper by somebody else, the DOI was mistyped, and without this the wrong
reference enters looking exactly like a right one (SPEC §5.0).

Metadata a caller asserts — for the sources translators read badly or not at
all — is merged in records.py, and shown field by field in the plan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pyzotero import zotero

from . import records as rec
from .models import Binding, IngestEvent, IngestPlan, utcnow  # noqa: F401
from .translation import (
    Translation,
    TranslationError,
    brief,
    classify,
    first_author,
    normalise,
)
from .zotero_client import Zot, parse_library

# Item types where the translators are least reliable, so the type is put in
# front of a person rather than trusted. Measured: 140 `document` items in a
# real library carried one DOI between them — that is the drawer things land in
# when nothing classified them.
UNCERTAIN_TYPES = {"document", "webpage", "report", "manuscript", "presentation"}

# The dedup index reads the whole collection. It used to stop at 500, which one
# book's bibliography passes: every item after the 500th came back `new`.
POOL_LIMIT = 20_000

ENTRY_KEYS = {"id", "hint", "ref", "ref_key", "item_type", "metadata"}

_fold = rec.fold


@dataclass
class Row:
    """One line of a plan: what would happen to one source."""

    identifier: str
    kind: str | None = None
    outcome: str = "unresolved"  # new | present | ambiguous | unresolved | invalid
    item_type: str | None = None
    metadata: dict = field(default_factory=dict)
    existing: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    hint: str | None = None
    warnings: list[str] = field(default_factory=list)
    # The item exactly as it will be written: the translator's output, merged
    # with whatever the caller asserted. Applying a plan writes this — not a
    # fresh lookup that may have drifted from what a person read and agreed to.
    raw: dict = field(default_factory=dict)
    # Where the item came from: translator | translator+caller | caller.
    source: str = "translator"
    ref_key: str | None = None
    ref: str | None = None
    overridden: list[dict] = field(default_factory=list)
    asserted: dict = field(default_factory=dict)
    dropped: dict = field(default_factory=dict)
    defaulted: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "identifier": self.identifier,
            "kind": self.kind,
            "outcome": self.outcome,
            "item_type": self.item_type,
            "metadata": self.metadata,
            "existing": self.existing,
            "candidates": self.candidates,
            "hint": self.hint,
            "warnings": self.warnings,
            "raw": self.raw,
            "source": self.source,
            "ref_key": self.ref_key,
            "ref": self.ref,
            "overridden": self.overridden,
            "asserted": self.asserted,
            "dropped": self.dropped,
            "defaulted": self.defaulted,
        }


class _Index:
    """Lookup tables over a collection, plus the rows already planned as new.

    The second half matters on a batch: the same paper reached once by its DOI
    and once by its PubMed Central URL would otherwise come out as two `new`
    rows, and the unique row on (binding, identifier) would let both through,
    because the identifiers differ.
    """

    def __init__(self, items: list[dict]):
        self.by_doi: dict[str, dict] = {}
        self.by_isbn: dict[str, list[dict]] = {}
        self.by_url: dict[str, list[dict]] = {}
        self.by_title: dict[str, list[dict]] = {}
        for it in items:
            csl = it.get("csljson") or {}
            self._add(it, it.get("doi"), it.get("isbn") or csl.get("ISBN"),
                      it.get("url") or csl.get("URL"), it.get("title"))

    def _add(self, it: dict, doi, isbn, url, title) -> None:
        if doi:
            self.by_doi[doi.lower()] = it
        for n in rec.isbns(isbn or ""):
            self.by_isbn.setdefault(n, []).append(it)
        u = rec.url_key(url)
        if u:
            self.by_url.setdefault(u, []).append(it)
        t = _fold(title)
        if t:
            self.by_title.setdefault(t, []).append(it)

    def add_planned(self, row: Row, position: int) -> None:
        item = row.raw
        title = rec.field_of(item, "title") or ""
        entry = {
            "key": None, "library": "this plan", "plan_row": position,
            "identifier": row.identifier, "title": title,
            "year": rec.year_of(rec.field_of(item, "date")),
        }
        self._add(entry, item.get("DOI"), item.get("ISBN"), _url_of(row), title)


def _url_of(row: Row) -> str | None:
    return row.raw.get("url") or (row.identifier if row.kind == "url" else None)


def _existing(twin: dict, match: str) -> dict:
    if twin.get("plan_row") is not None:
        return {"plan_row": twin["plan_row"], "identifier": twin["identifier"],
                "match": match}
    return {"key": twin["key"], "library": twin["library"], "match": match}


def _candidate(c: dict) -> dict:
    out = {"key": c["key"], "library": c["library"], "title": (c["title"] or "")[:90],
           "year": c.get("year")}
    if c.get("plan_row") is not None:
        out["plan_row"] = c["plan_row"]
    return out


def _dedup(row: Row, idx: _Index) -> None:
    """DOI, then ISBN, then URL, then a fuzzy title — and the last one asks."""
    item = row.raw
    title = rec.field_of(item, "title")

    doi = (item.get("DOI") or "").lower()
    if doi and doi in idx.by_doi:
        row.outcome, row.existing = "present", _existing(idx.by_doi[doi], "doi")
        return

    # An ISBN names a book, and every chapter of an edited volume carries the
    # same one: it marks a duplicate only when the titles agree as well.
    for n in rec.isbns(item.get("ISBN") or ""):
        for twin in idx.by_isbn.get(n, []):
            if rec.titles_agree(title, twin.get("title")):
                row.outcome, row.existing = "present", _existing(twin, "isbn")
                return

    # Same rule for a URL, for the same reason: a publisher's page for a book is
    # also the URL people give for each of its chapters.
    u = rec.url_key(_url_of(row))
    same_url = idx.by_url.get(u, []) if u else []
    for twin in same_url:
        if rec.titles_agree(title, twin.get("title")):
            row.outcome, row.existing = "present", _existing(twin, "url")
            return
    if same_url:
        row.outcome = "ambiguous"
        row.candidates = [_candidate(c) for c in same_url]
        row.warnings.append(
            "same URL as an existing item but a different title — often a "
            "publisher's page standing for several chapters. catena does not "
            "decide this: confirm they differ, or add it anyway."
        )
        return

    near = idx.by_title.get(_fold(title), [])
    if near:
        row.outcome = "ambiguous"
        row.candidates = [_candidate(c) for c in near]
        row.warnings.append(
            "same title, no shared identifier. catena does not decide this: "
            "confirm it is the same paper, or add it anyway."
        )
        return

    row.outcome = "new"


def _entries(identifiers, hints, entries) -> list[dict]:
    out = [{"id": i, "hint": (hints or {}).get(i)} for i in identifiers or []]
    return out + [dict(e) for e in entries or []]


def _plan_one(entry: dict, tr: Translation, schema, today) -> Row:
    """Resolve one source and merge what the caller asserted. No dedup here."""
    ident = (entry.get("id") or "").strip()
    item_type = (entry.get("item_type") or "").strip() or None
    metadata = entry.get("metadata") or None
    caller = bool(item_type or metadata)
    row = Row(identifier=ident, hint=entry.get("hint") or None,
              ref=entry.get("ref") or None, ref_key=entry.get("ref_key") or None)

    unknown = set(entry) - ENTRY_KEYS
    if unknown:
        row.outcome = "invalid"
        row.warnings.append(
            f"unknown entry keys: {', '.join(sorted(unknown))}. An entry takes "
            f"{', '.join(sorted(ENTRY_KEYS))}."
        )
        return row
    if not ident and not caller:
        row.outcome = "invalid"
        row.warnings.append("an entry needs an id, or an item_type and metadata, or both")
        return row

    translated = None
    if ident:
        kind = classify(ident)
        try:
            res = tr.resolve(ident)
            translated = res.item or {}
            row.kind, row.identifier = res.kind, res.identifier
        except TranslationError as e:
            row.warnings.append(str(e))
            if kind is None:
                if caller:
                    row.outcome = "invalid"
                    row.warnings.append(
                        "leave id out for a source that has none; the metadata "
                        "alone creates it."
                    )
                return row
            row.kind, row.identifier = kind, normalise(ident, kind)
            if kind in ("doi", "pmid", "arxiv"):
                # Registered identifiers resolve. One that does not was copied
                # wrong, and dressing it in caller metadata would file a wrong
                # DOI under a right-looking reference.
                row.warnings.append(
                    f"a {kind} that does not resolve was probably copied wrong. "
                    "Fix it, or drop the id and let the metadata stand alone."
                )
                return row
            if not caller:
                if kind == "url" and rec.is_pdf(row.identifier):
                    row.warnings.append(
                        "this is a PDF, and translators rarely read those. Give "
                        "item_type and metadata, and the PDF's URL stays on the item."
                    )
                elif kind == "isbn":
                    row.warnings.append(
                        "no catalogue the translator asks knows this ISBN. Give "
                        "item_type 'book' and metadata, and the ISBN is kept."
                    )
                return row
            row.warnings.append(
                "the translator could not read it; the item is built from the "
                "caller's metadata alone."
            )

    if caller:
        fill = {}
        if row.kind == "url":
            fill["url"] = row.identifier
        elif row.kind == "isbn":
            fill["ISBN"] = row.identifier
        try:
            m = rec.merge(translated, item_type, metadata, schema, fill=fill, today=today)
        except rec.RecordError as e:
            row.outcome = "invalid"
            row.warnings.append(str(e))
            return row
        item = m["item"]
        row.overridden, row.asserted = m["overridden"], m["asserted"]
        row.dropped, row.defaulted = m["dropped"], m["defaulted"]
        row.source = "translator+caller" if translated else "caller"
    else:
        item = translated

    row.raw = item
    row.item_type = item.get("itemType")
    row.metadata = brief(item)
    if not row.identifier:
        row.identifier, row.kind = rec.synthetic_identifier(item), "manual"

    if row.item_type in UNCERTAIN_TYPES and not item_type:
        row.warnings.append(
            f"the translator called this a {row.item_type!r}. CSL formats by "
            "type, so a report filed as a webpage comes out wrong in every "
            "style — worth a look before it goes in, or set item_type."
        )
    if row.source == "translator" and row.kind == "url" and rec.is_pdf(row.identifier):
        row.warnings.append(
            "this came from a PDF, which translators read poorly: check title "
            "and authors, or give metadata."
        )
    if row.dropped:
        row.warnings.append(
            "changing the type left translator fields with nowhere to go: "
            f"{', '.join(sorted(row.dropped))}. Supply them if they matter."
        )
    caller_title = (metadata or {}).get("title")
    if translated and caller_title and not rec.titles_agree(
        rec.field_of(translated, "title"), caller_title
    ):
        row.warnings.append(
            "the caller's title and the translator's "
            f"({rec.field_of(translated, 'title')!r}) disagree. That usually means "
            "the identifier points at something else — another edition, or the "
            "book instead of the chapter."
        )
    if row.source == "caller":
        row.warnings.append(
            "built from the caller's metadata alone: nothing checked it against "
            f"the source. It goes in tagged {rec.CALLER_TAG}."
        )

    if row.hint:
        got = first_author(translated or item)
        if got and _fold(row.hint) not in _fold(got) and _fold(got) not in _fold(row.hint):
            row.warnings.append(
                f"the draft says {row.hint!r} but {row.identifier} resolves to "
                f"{got!r}. That usually means the identifier was copied wrong."
            )
    row.outcome = "new"  # until the dedup says otherwise
    return row


def build_plan(
    zot: Zot,
    tr: Translation,
    binding: Binding,
    identifiers: list[str] | None,
    hints: dict[str, str] | None = None,
    *,
    entries: list[dict] | None = None,
    existing: list[dict] | None = None,
    schema=None,
    today=None,
) -> list[Row]:
    """Resolve everything, decide nothing. Reads only — no write happens here.

    `identifiers` and `hints` are the plain form; `entries` the full one, where a
    source can carry an item type and metadata that win over the translator, or
    stand alone when nothing resolves it. `schema` answers which fields an item
    type has; it defaults to the caller's Zotero.
    """
    pool = existing if existing is not None else _collection_pool(zot, binding)
    idx = _Index(pool)
    schema = schema or zot

    rows: list[Row] = []
    for position, entry in enumerate(_entries(identifiers, hints, entries)):
        row = _plan_one(entry, tr, schema, today)
        if row.outcome not in ("unresolved", "invalid"):
            _dedup(row, idx)
            if row.outcome == "new":
                idx.add_planned(row, position)
        rows.append(row)
    return rows


def _collection_pool(zot: Zot, binding: Binding) -> list[dict]:
    pool: list[dict] = []
    if binding.source_collection_key:
        pool += zot.collection_items(
            binding.source_library, binding.source_collection_key, POOL_LIMIT
        )
    if binding.deposit_collection_key and not binding.one_legged:
        pool += zot.collection_items(
            binding.deposit_library, binding.deposit_collection_key, POOL_LIMIT
        )
    return pool


def save_plan(db, binding: Binding, rows: list[Row]) -> IngestPlan:
    plan = IngestPlan(
        binding_id=binding.id,
        payload=json.dumps([r.as_dict() for r in rows], ensure_ascii=False),
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


BEARING_TAGS = {
    "supports": "catena:supports",
    "supports_directly": "catena:supports",
    "supports_indirectly": "catena:supports",
    "contradicts": "catena:contradicts",
    "qualifies": "catena:qualifies",
    "mixed": "catena:qualifies",
}


def provenance_note(run_id: str, verdict: str, passages: list[dict]) -> str:
    """The reason a paper is cited, attached to the paper itself (SPEC §6).

    This is the point of the whole tool. A verification produces verbatim
    passages and a verdict, and today they live only in Contrarian's trace; put
    them on the Zotero item and the answer to "why is this cited here" sits two
    clicks from the citation, and travels to co-authors with the group library.
    """
    lines = [
        "<p><b>Verified with Contrarian</b> — "
        f"verdict <i>{verdict}</i>, run <code>{run_id}</code>.</p>",
        f'<p><a href="https://contrarian.borant.eu/runs/{run_id}">full trace</a></p>',
    ]
    for p in passages or []:
        quote = (p.get("quote") or "").strip()
        where = p.get("location") or "?"
        bearing = p.get("bearing") or "?"
        why = p.get("why") or ""
        lines.append(
            f"<blockquote><p>{quote}</p>"
            f"<p><i>{where} — {bearing}</i>{(' · ' + why) if why else ''}</p></blockquote>"
        )
    return "".join(lines)


def _skip_reason(row: Row, force_ambiguous: bool) -> str | None:
    if row.outcome == "present":
        if (row.existing or {}).get("plan_row") is not None:
            return "the same item comes earlier in this plan"
        return "already in the collection"
    if row.outcome == "unresolved":
        return "nothing resolved it"
    if row.outcome == "invalid":
        return "its metadata does not fit Zotero: " + (row.warnings[-1] if row.warnings else "?")
    if row.outcome == "ambiguous" and not force_ambiguous:
        return "same title or URL as an existing item; not decided"
    return None


def apply_plan(
    db, zot: Zot, binding: Binding, plan: IngestPlan, *, force_ambiguous: bool = False,
    provenance: dict | None = None,
) -> dict:
    """Execute exactly what the plan declared, once.

    A plan that has already been applied is refused rather than replayed: that
    is the second half of the idempotency, the half that catches a retry of the
    apply itself rather than of a single item.
    """
    if plan.applied_at:
        return {
            "error": f"plan {plan.id} was already applied at "
            f"{plan.applied_at:%Y-%m-%d %H:%M}. Build a new one rather than "
            "replaying this."
        }

    known = set(Row.__dataclass_fields__)
    rows = [Row(**{k: v for k, v in r.items() if k in known})
            for r in json.loads(plan.payload)]
    ident, kind = parse_library(binding.deposit_library)
    client = zotero.Zotero(ident, kind, zot.api_key)

    added, skipped, failed = [], [], []
    for row in rows:
        why = _skip_reason(row, force_ambiguous)
        if why:
            skipped.append({"identifier": row.identifier, "ref_key": row.ref_key, "why": why})
            continue

        # The idempotency key goes in *before* the write. If the POST succeeds
        # and the process dies before the key is recorded, the row is already
        # there with no item_key — a reingest to reconcile, not a duplicate.
        event = IngestEvent(
            binding_id=binding.id,
            identifier=row.identifier,
            identifier_kind=row.kind or "unknown",
            item_library=binding.deposit_library,
            source="contrarian" if provenance else "manual",
            run_id=(provenance or {}).get("run_id"),
            verdict=(provenance or {}).get("verdict"),
        )
        db.add(event)
        try:
            db.commit()
        except Exception:
            db.rollback()
            skipped.append({"identifier": row.identifier, "ref_key": row.ref_key,
                            "why": "already ingested into this binding"})
            continue

        try:
            item = _to_zotero_item(row, binding.deposit_collection_key, provenance)
            resp = client.create_items([item])
            key = _created_key(resp)
            if not key:
                raise RuntimeError(f"Zotero refused it: {json.dumps(resp)[:200]}")
            event.item_key = key
            db.commit()

            noted = False
            if provenance and provenance.get("passages"):
                try:
                    client.create_items([{
                        "itemType": "note",
                        "parentItem": key,
                        "note": provenance_note(
                            provenance.get("run_id", "?"),
                            provenance.get("verdict", "?"),
                            provenance["passages"],
                        ),
                    }])
                    noted = True
                except Exception as e:  # noqa: BLE001
                    failed.append({"identifier": row.identifier,
                                   "error": f"item created, note failed: {str(e)[:120]}"})
            added.append({"identifier": row.identifier, "ref_key": row.ref_key, "key": key,
                          "title": row.metadata.get("title"),
                          "source": row.source,
                          "provenance_note": noted})
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            db.rollback()
            failed.append({"identifier": row.identifier, "ref_key": row.ref_key,
                           "error": str(e)[:200]})

    plan.applied_at = utcnow()
    db.commit()
    return {"plan": plan.id, "added": added, "skipped": skipped, "failed": failed}


# Fields the translator emits that belong to *its* copy of the item and would
# collide with a real one.
_STRIP = {"key", "version", "dateAdded", "dateModified", "relations", "collections"}


def _to_zotero_item(row: Row, collection_key: str | None,
                    provenance: dict | None = None) -> dict:
    """The planned item, cleaned and filed straight into the collection.

    Creating it already in the collection rather than adding it afterwards
    removes a second write that could half-succeed and leave an item nobody can
    find.
    """
    if not row.raw:
        raise RuntimeError(
            "the plan carries no translator output for this identifier; rebuild it"
        )
    item = {k: v for k, v in row.raw.items() if k not in _STRIP}
    if collection_key:
        item["collections"] = [collection_key]
    tags = list(item.get("tags") or [])
    if row.source != "translator":
        # Findable later: every reference a person, and not a translator, stood
        # behind — the set worth a second look before the book goes to print.
        tags.append({"tag": rec.CALLER_TAG})
    if provenance:
        # The tag carries the *bearing*, not just the provenance: a paper that
        # contradicts you belongs in the bibliography — that is half the point of
        # verifying — but it must not be cited as though it agreed.
        tag = BEARING_TAGS.get((provenance.get("verdict") or "").lower())
        if tag:
            tags.append({"tag": tag})
    if tags:
        item["tags"] = tags
    return item


def _created_key(resp: dict) -> str | None:
    ok = (resp or {}).get("successful") or {}
    for v in ok.values():
        return v.get("key") or (v.get("data") or {}).get("key")
    return None
