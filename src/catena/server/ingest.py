"""
Adding items to Zotero, and the three guards that stand in front of it.

Writing to somebody's library is the only thing catena does that a person
cannot undo by closing a file. So each write passes three checks, and each of
them exists because of a specific way this goes wrong.

**Idempotency.** pyzotero sends a fresh `Zotero-Write-Token` on every call, which
covers its own internal retries but not a second call from the caller — and the
caller here is a model that may retry a tool whose answer got lost. The
`UNIQUE (binding_id, identifier)` row in `ingest_events` is the protection,
because it lives at the level where the retry happens (SPEC §9.1).

**Deduplication.** DOI, then ISBN, then a fuzzy title match — and on the fuzzy
rung it does not decide. With tens of thousands of records in a library, the
cost of one question is smaller than the cost of one duplicate, and a duplicate
here is not cosmetic: the same paper under two URIs produces two bibliography
entries and, in an author-date style, a 2008a/2008b that does not exist.

**The author cross-check.** When a draft's citation reads `[Assan, 10.1136/…]`,
the surname is not an identifier — it is a *check*. If the DOI resolves to a
paper by somebody else, the DOI was mistyped, and without this the wrong
reference enters looking exactly like a right one (SPEC §5.0).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from pyzotero import zotero

from .models import Binding, IngestEvent, IngestPlan, utcnow  # noqa: F401
from .translation import Translation, TranslationError, brief, first_author
from .zotero_client import Zot, parse_library

# Item types where the translators are least reliable, so the type is put in
# front of a person rather than trusted. Measured: 140 `document` items in a
# real library carried one DOI between them — that is the drawer things land in
# when nothing classified them.
UNCERTAIN_TYPES = {"document", "webpage", "report", "manuscript", "presentation"}


def _fold(text: str) -> str:
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text or "") if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


@dataclass
class Row:
    """One line of a plan: what would happen to one identifier."""

    identifier: str
    kind: str | None = None
    outcome: str = "unresolved"  # new | present | ambiguous | unresolved
    item_type: str | None = None
    metadata: dict = field(default_factory=dict)
    existing: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    hint: str | None = None
    warnings: list[str] = field(default_factory=list)
    # The translator's own output, carried through so that applying a plan
    # writes exactly what was resolved and shown — not a fresh lookup that may
    # have drifted, and never anything assembled by hand.
    raw: dict = field(default_factory=dict)

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
        }


def _index(items: list[dict]) -> tuple[dict, dict, dict]:
    """Three lookup tables over a collection: by DOI, by ISBN, by folded title."""
    by_doi, by_isbn, by_title = {}, {}, {}
    for it in items:
        if it.get("doi"):
            by_doi[it["doi"].lower()] = it
        csl = it.get("csljson") or {}
        isbn = (csl.get("ISBN") or "").replace("-", "").replace(" ", "")
        if isbn:
            by_isbn[isbn] = it
        t = _fold(it.get("title"))
        if t:
            by_title.setdefault(t, []).append(it)
    return by_doi, by_isbn, by_title


def build_plan(
    zot: Zot,
    tr: Translation,
    binding: Binding,
    identifiers: list[str],
    hints: dict[str, str] | None = None,
    *,
    existing: list[dict] | None = None,
) -> list[Row]:
    """Resolve everything, decide nothing. Reads only — no write happens here."""
    hints = hints or {}
    pool = existing if existing is not None else _collection_pool(zot, binding)
    by_doi, by_isbn, by_title = _index(pool)

    rows: list[Row] = []
    for raw in identifiers:
        row = Row(identifier=raw, hint=hints.get(raw))
        try:
            res = tr.resolve(raw)
        except TranslationError as e:
            row.outcome = "unresolved"
            row.warnings.append(str(e))
            rows.append(row)
            continue

        item = res.item or {}
        row.kind = res.kind
        row.identifier = res.identifier
        row.item_type = res.item_type
        row.metadata = brief(item)
        row.raw = item

        if row.item_type in UNCERTAIN_TYPES:
            row.warnings.append(
                f"the translator called this a {row.item_type!r}. CSL formats by "
                "type, so a report filed as a webpage comes out wrong in every "
                "style — worth a look before it goes in."
            )

        if row.hint:
            got = first_author(item)
            if got and _fold(row.hint) not in _fold(got) and _fold(got) not in _fold(row.hint):
                row.warnings.append(
                    f"the draft says {row.hint!r} but {res.identifier} resolves to "
                    f"{got!r}. That usually means the identifier was copied wrong."
                )

        doi = (item.get("DOI") or "").lower()
        isbn = (item.get("ISBN") or "").replace("-", "").replace(" ", "")
        twin = by_doi.get(doi) if doi else None
        twin = twin or (by_isbn.get(isbn) if isbn else None)
        if twin:
            row.outcome = "present"
            row.existing = {"key": twin["key"], "library": twin["library"], "match": "exact"}
            rows.append(row)
            continue

        near = by_title.get(_fold(item.get("title")), [])
        if near:
            row.outcome = "ambiguous"
            row.candidates = [
                {"key": c["key"], "library": c["library"], "title": c["title"][:90],
                 "year": c["year"]}
                for c in near
            ]
            row.warnings.append(
                "same title, no shared identifier. catena does not decide this: "
                "confirm it is the same paper, or add it anyway."
            )
            rows.append(row)
            continue

        row.outcome = "new"
        rows.append(row)
    return rows


def _collection_pool(zot: Zot, binding: Binding) -> list[dict]:
    pool: list[dict] = []
    if binding.source_collection_key:
        pool += zot.collection_items(binding.source_library, binding.source_collection_key, 500)
    if binding.deposit_collection_key and not binding.one_legged:
        pool += zot.collection_items(binding.deposit_library, binding.deposit_collection_key, 500)
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

    rows = [Row(**{k: v for k, v in r.items()}) for r in json.loads(plan.payload)]
    ident, kind = parse_library(binding.deposit_library)
    client = zotero.Zotero(ident, kind, zot.api_key)

    added, skipped, failed = [], [], []
    for row in rows:
        if row.outcome == "present":
            skipped.append({"identifier": row.identifier, "why": "already in the collection"})
            continue
        if row.outcome == "unresolved":
            skipped.append({"identifier": row.identifier, "why": "nothing resolved it"})
            continue
        if row.outcome == "ambiguous" and not force_ambiguous:
            skipped.append(
                {"identifier": row.identifier, "why": "same title as an existing item; not decided"}
            )
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
            skipped.append(
                {"identifier": row.identifier, "why": "already ingested into this binding"}
            )
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
            added.append({"identifier": row.identifier, "key": key,
                          "title": row.metadata.get("title"),
                          "provenance_note": noted})
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            db.rollback()
            failed.append({"identifier": row.identifier, "error": str(e)[:200]})

    plan.applied_at = utcnow()
    db.commit()
    return {"plan": plan.id, "added": added, "skipped": skipped, "failed": failed}


# Fields the translator emits that belong to *its* copy of the item and would
# collide with a real one.
_STRIP = {"key", "version", "dateAdded", "dateModified", "relations", "collections"}


def _to_zotero_item(row: Row, collection_key: str | None,
                    provenance: dict | None = None) -> dict:
    """The translator's item, cleaned and filed straight into the collection.

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
    if provenance:
        # The tag carries the *bearing*, not just the provenance: a paper that
        # contradicts you belongs in the bibliography — that is half the point of
        # verifying — but it must not be cited as though it agreed.
        tag = BEARING_TAGS.get((provenance.get("verdict") or "").lower())
        if tag:
            item["tags"] = (item.get("tags") or []) + [{"tag": tag}]
    return item


def _created_key(resp: dict) -> str | None:
    ok = (resp or {}).get("successful") or {}
    for v in ok.values():
        return v.get("key") or (v.get("data") or {}).get("key")
    return None
