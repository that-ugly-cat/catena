"""
Metadata a caller asserts, and how it meets what the translator found.

SPEC §5 says catena never invents an item, and that rule stands: what it guards
against is a model filling in a title and a journal *from memory*. What this
module admits is different — metadata a person checked against the source and
wrote down, for the sources no translator reads well: newspaper pages filed as
`webpage`, PDF reports, laws, lectures, books whose ISBN nobody indexed.

It is admitted on two conditions, and both are the point:

- **It never passes silently.** Every field the caller supplies is listed in the
  plan next to what the translator said, so the confirmation is a diff and not
  an act of faith. A caller title that disagrees with the translator's is a
  warning, because it usually means the identifier points at something else.
- **It stays findable.** Items carrying caller fields are tagged
  `catena:caller-metadata`, so a later pass in Zotero can find every reference
  whose metadata a person, and not a translator, stood behind.

The caller writes a small neutral vocabulary (`container`, `publisher`,
`number`…) and catena maps it onto the field the item type actually has —
`websiteTitle` on a webpage, `bookTitle` on a chapter, `nameOfAct` and
`dateEnacted` on a statute. The mapping is checked against Zotero's own field
list for the type, at plan time: a field Zotero would refuse comes back as a
readable row, not as a 400 halfway through an apply.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import unicodedata
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

CALLER_TAG = "catena:caller-metadata"

# Zotero's base-field families: one meaning, a different field name per item
# type. The first member valid for a type is the one a neutral key lands on, and
# a type change carries a value across the family instead of dropping it.
FAMILIES: dict[str, list[str]] = {
    "title": ["title", "nameOfAct", "caseName", "subject"],
    "date": ["date", "dateEnacted", "dateDecided", "issueDate", "filingDate"],
    "container": [
        "publicationTitle", "websiteTitle", "blogTitle", "bookTitle",
        "proceedingsTitle", "forumTitle", "programTitle", "encyclopediaTitle",
        "dictionaryTitle", "code", "reporter",
    ],
    "publisher": [
        "publisher", "institution", "university", "studio", "label", "network",
        "distributor", "company", "legislativeBody", "court",
    ],
    "number": [
        "number", "reportNumber", "publicLawNumber", "billNumber",
        "episodeNumber", "patentNumber", "docketNumber",
    ],
    "kind": [
        "reportType", "websiteType", "presentationType", "letterType",
        "thesisType", "manuscriptType", "postType", "genre",
    ],
}

# Neutral keys that map to exactly one Zotero field.
PLAIN = {
    "place": "place",
    "url": "url",
    "accessed": "accessDate",
    "isbn": "ISBN",
    "doi": "DOI",
    "pages": "pages",
    "volume": "volume",
    "issue": "issue",
    "edition": "edition",
    "series": "series",
    "language": "language",
    "abstract": "abstractNote",
    "short_title": "shortTitle",
    "meeting": "meetingName",
}

METADATA_KEYS = set(FAMILIES) | set(PLAIN) | {"authors", "editors", "fields"}

# Keys of a Zotero item that are structure, not fields of its type. The last
# four are the translator's bookkeeping: carried through and stripped at write,
# never reported as "dropped", where they read as data lost.
STRUCTURAL = {"itemType", "creators", "tags", "collections", "relations", "notes",
              "attachments", "seeAlso", "key", "version", "dateAdded", "dateModified"}


class Schema(Protocol):
    """What catena needs to know about Zotero's item types. Reads only."""

    def fields(self, item_type: str) -> list[str]: ...

    def creator_types(self, item_type: str) -> list[str]:
        """Allowed creator types, primary first."""
        ...


class RecordError(ValueError):
    pass


def fold(text: str) -> str:
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text or "") if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


def field_of(item: dict, family: str) -> str | None:
    """The value of a family on any item type: a statute's title is `nameOfAct`."""
    for name in FAMILIES[family]:
        if item.get(name):
            return item[name]
    return None


def year_of(date: str | None) -> str | None:
    """The year in a Zotero date, whatever its shape.

    Translators hand back "2006-06-26T19:12:47Z", "10/2006" or "June 1964";
    the first four characters of those are a year only in the first case.
    """
    m = re.search(r"(?<!\d)(\d{4})(?!\d)", date or "")
    return m.group(1) if m else None


def titles_agree(a: str | None, b: str | None) -> bool:
    """Loose on purpose: subtitles, articles and punctuation come and go."""
    fa, fb = fold(a or ""), fold(b or "")
    if not fa or not fb:
        return True
    if fa in fb or fb in fa:
        return True
    ta, tb = set(fa.split()), set(fb.split())
    return len(ta & tb) / max(1, min(len(ta), len(tb))) >= 0.6


# --- identifiers -----------------------------------------------------------------


def isbn13(raw: str) -> str | None:
    """ISBN-10 or -13 to ISBN-13, or None if it is not one."""
    s = re.sub(r"[^0-9Xx]", "", raw or "").upper()
    if len(s) == 13 and s.isdigit():
        return s
    if len(s) == 10 and s[:9].isdigit():
        core = "978" + s[:9]
        total = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(core))
        return core + str((10 - total % 10) % 10)
    return None


def isbns(raw: str) -> set[str]:
    """Every ISBN in a field. Zotero keeps several in one string, space-separated."""
    out = set()
    for part in re.split(r"[\s,;]+", raw or ""):
        n = isbn13(part)
        if n:
            out.add(n)
    return out


_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|mc_.*|ref_src)$", re.I)


def url_key(url: str | None) -> str | None:
    """A URL reduced to what identifies the page.

    Scheme, `www.`, a trailing slash, the fragment and tracking parameters do not
    make a page a different page. The rest of the query does: EUR-Lex names its
    documents in it.
    """
    if not url or not re.match(r"^https?://", url.strip(), re.I):
        return None
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode(sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _TRACKING.match(k)
    ))
    return urlunsplit(("", host, parts.path.rstrip("/"), query, "")).lstrip("/")


def is_pdf(url: str | None) -> bool:
    return bool(url) and urlsplit(url).path.lower().endswith(".pdf")


def synthetic_identifier(item: dict) -> str:
    """A stable identifier for a source that has none.

    The idempotency row is keyed on (binding, identifier), so a record with no
    DOI, ISBN or URL still needs one — and it has to come out the same on a
    retry, or the retry adds a second copy. Title, year and first creator, folded.
    """
    first = ""
    for c in item.get("creators") or []:
        first = c.get("lastName") or c.get("name") or ""
        if first:
            break
    basis = "|".join([
        fold(field_of(item, "title") or ""),
        year_of(field_of(item, "date")) or "",
        fold(first),
    ])
    return "ref:" + hashlib.sha1(basis.encode()).hexdigest()[:16]


# --- the record --------------------------------------------------------------------


def _creator(value, creator_type: str) -> dict:
    """`"Family, Given"` is a person; a string without a comma is an institution."""
    if isinstance(value, str):
        v = value.strip()
        if not v:
            raise RecordError("an empty name among the creators")
        if "," in v:
            last, first = (p.strip() for p in v.split(",", 1))
            return {"creatorType": creator_type, "lastName": last, "firstName": first}
        return {"creatorType": creator_type, "name": v}
    if isinstance(value, dict):
        last = value.get("family") or value.get("lastName")
        first = value.get("given") or value.get("firstName") or ""
        name = value.get("name") or value.get("literal")
        if last:
            return {"creatorType": creator_type, "lastName": last, "firstName": first}
        if name:
            return {"creatorType": creator_type, "name": name}
    raise RecordError(
        f"cannot read {value!r} as a creator: write \"Family, Given\" for a person, "
        "a plain string or {\"name\": ...} for an institution"
    )


def merge(
    translated: dict | None,
    item_type: str | None,
    metadata: dict | None,
    schema: Schema,
    *,
    fill: dict | None = None,
    today: _dt.date | None = None,
) -> dict:
    """The item that will be written, and an account of where each part came from.

    Returns `item`, `overridden` (fields where the caller beat the translator,
    both values shown), `asserted` (fields only the caller gave), `dropped`
    (translator fields the new type has no room for) and `defaulted`. `fill`
    holds values that come from the identifier itself — the URL that was
    resolved, the ISBN that was looked up — and lands only where nothing else
    did. Raises RecordError on anything Zotero would refuse.
    """
    metadata = dict(metadata or {})
    unknown = set(metadata) - METADATA_KEYS
    if unknown:
        raise RecordError(
            f"unknown metadata keys: {', '.join(sorted(unknown))}. Use "
            f"{', '.join(sorted(METADATA_KEYS))}; anything else goes in fields{{}} "
            "under its Zotero name."
        )
    source_type = (translated or {}).get("itemType")
    target = item_type or source_type
    if not target:
        raise RecordError("item_type is required when nothing resolved the source")
    try:
        valid = set(schema.fields(target))
        creator_types = schema.creator_types(target)
    except Exception as e:  # noqa: BLE001 - an unknown type is the usual cause
        raise RecordError(f"{target!r} is not a Zotero item type ({e})") from e
    primary = creator_types[0] if creator_types else "author"

    def slot(family: str) -> str | None:
        return next((f for f in FAMILIES[family] if f in valid), None)

    item: dict = {}
    dropped: dict = {}
    if translated:
        if source_type == target:
            item = dict(translated)
        else:
            src_primary = (schema.creator_types(source_type) or ["author"])[0]
            family_of = {f: fam for fam, names in FAMILIES.items() for f in names}
            for k, v in translated.items():
                if k == "creators":
                    kept = []
                    for c in v or []:
                        ct = c.get("creatorType")
                        if ct == src_primary:
                            kept.append(dict(c, creatorType=primary))
                        elif ct in creator_types:
                            kept.append(c)
                        else:
                            dropped.setdefault("creators", []).append(c)
                    item["creators"] = kept
                elif k in STRUCTURAL or k in valid:
                    item[k] = v
                elif k in family_of and slot(family_of[k]):
                    item.setdefault(slot(family_of[k]), v)
                elif v not in (None, "", []):
                    dropped[k] = v
    item["itemType"] = target

    overridden: list[dict] = []
    asserted: dict = {}

    def put(field: str, value) -> None:
        before = item.get(field)
        if before not in (None, "", []) and before != value:
            overridden.append({"field": field, "translator": before, "caller": value})
        elif before in (None, "", []):
            asserted[field] = value
        item[field] = value

    for family in FAMILIES:
        if metadata.get(family) not in (None, ""):
            f = slot(family)
            if not f:
                raise RecordError(
                    f"a {target} has no field for {family!r}. Put it in "
                    f"fields{{}} under one of: {', '.join(sorted(valid))}"
                )
            put(f, str(metadata[family]))
    for key, f in PLAIN.items():
        if metadata.get(key) not in (None, ""):
            if f not in valid:
                raise RecordError(f"a {target} has no {f!r} field (from {key!r})")
            value = str(metadata[key])
            put(f, (isbn13(value) or value) if key == "isbn" else value)
    for f, value in (metadata.get("fields") or {}).items():
        if f not in valid:
            raise RecordError(
                f"{f!r} is not a field of {target}. Valid: {', '.join(sorted(valid))}"
            )
        put(f, value)

    for key, role in (("authors", primary), ("editors", "editor")):
        if key not in metadata:
            continue
        if role not in creator_types:
            raise RecordError(f"a {target} has no {role!r} creators")
        new = [_creator(v, role) for v in metadata[key] or []]
        old = [c for c in item.get("creators") or [] if c.get("creatorType") == role]
        rest = [c for c in item.get("creators") or [] if c.get("creatorType") != role]
        if old and old != new:
            overridden.append({"field": key, "translator": old, "caller": new})
        elif not old and new:
            asserted[key] = new
        item["creators"] = (new + rest) if role == primary else (rest + new)

    if not field_of(item, "title"):
        raise RecordError("no title: a reference without one cannot be cited")

    defaulted = {}
    for f, value in (fill or {}).items():
        if value and f in valid and not item.get(f):
            item[f] = value
            defaulted[f] = value

    # Agreed with Spit (16 Oct 2026): a caller record with a URL and no access
    # date gets the ingest date, so the styles that print "accessed" have one.
    if (overridden or asserted) and item.get("url") and not item.get("accessDate") \
            and "accessDate" in valid:
        item["accessDate"] = (today or _dt.date.today()).isoformat()
        defaulted["accessDate"] = item["accessDate"]

    return {
        "item": item,
        "overridden": overridden,
        "asserted": asserted,
        "dropped": dropped,
        "defaulted": defaulted,
    }
