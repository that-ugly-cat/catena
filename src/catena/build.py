"""
Building Zotero fields, as opposed to reading them.

`fields.py` reads what Word documents contain; this writes what they should
contain. Both halves describe the same format — the one verified against a real
manuscript, reproduced in a fixture, and put through Word twice (SPEC §7, §12.2,
§12.3) — so when the format is wrong, it is wrong in one place and the round
trip catches it.

Nothing here talks to Zotero or to a database: it takes CSL-JSON and URIs and
returns strings. That is deliberate. The same functions serve the MCP surface,
which produces fields for a model to place, and the local injector, which
splices them into OOXML — and a shared, side-effect-free core is the only way
those two can be guaranteed to emit the same bytes.
"""

from __future__ import annotations

import json
import secrets
from html import escape

SCHEMA = "https://github.com/citation-style-language/schema/raw/master/csl-citation.json"

MARK_ITEM = "ADDIN ZOTERO_ITEM CSL_CITATION "
BIBLIOGRAPHY_FIELD = (
    'ADDIN ZOTERO_BIBL {"uncited":[],"omitted":[],"custom":[]} CSL_BIBLIOGRAPHY'
)

# Word truncates a custom property's value at 255 characters. Measured on the
# real manuscript: ZOTERO_PREF_1 is 255 unescaped and 288 escaped, so the limit
# is on the value and not on its XML serialisation. Split first, escape after.
PREF_CHUNK = 255

ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# Zotero's own locator labels. A locator with an unknown label is silently
# dropped by citeproc, which is the kind of failure nobody notices, so we refuse
# it instead.
LOCATOR_LABELS = {
    "page", "book", "chapter", "column", "figure", "folio", "issue", "line",
    "note", "opus", "paragraph", "part", "section", "sub verbo", "volume",
    "verse",
}


def new_citation_id() -> str:
    """Eight characters, fresh every time.

    Unique per *occurrence*, not per item: in the real manuscript all 172 were
    distinct, including where the same paper is cited six times (SPEC §7.1).
    """
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(8))


def item_uri(library: str, key: str) -> str:
    """`groups/6378365` + `NE3IZD4R` -> the URI Zotero resolves by.

    Never a `users/local/...` form: those resolve on one machine in the world
    and are orphans to every co-author (SPEC §7.2).
    """
    library = library.strip("/")
    if library.startswith("users/local/"):
        raise ValueError(
            f"refusing to build a local-profile URI ({library}): it would "
            "resolve for nobody but the machine that wrote it"
        )
    if not (library.startswith("users/") or library.startswith("groups/")):
        raise ValueError(f"library must be users/<id> or groups/<id>, got {library!r}")
    return f"http://zotero.org/{library}/items/{key}"


def citation_item(
    library: str,
    key: str,
    item_data: dict,
    *,
    locator: str | None = None,
    label: str | None = None,
    prefix: str | None = None,
    suffix: str | None = None,
    suppress_author: bool = False,
) -> dict:
    """One entry of `citationItems`.

    `id` is written for consistency with `itemData.id`, and is decorative: with
    `uris` present Zotero never reads it, and overwrites it after resolving
    (SPEC §7.7, confirmed in Word).
    """
    if label is not None and label not in LOCATOR_LABELS:
        raise ValueError(
            f"unknown locator label {label!r}; citeproc would drop it silently. "
            f"One of: {', '.join(sorted(LOCATOR_LABELS))}"
        )
    if label and not locator:
        raise ValueError("a locator label without a locator says nothing")

    entry: dict = {
        "id": item_data.get("id") or key,
        "uris": [item_uri(library, key)],
        "itemData": item_data,
    }
    if locator:
        entry["locator"] = str(locator)
        entry["label"] = label or "page"
    if prefix:
        entry["prefix"] = prefix
    if suffix:
        entry["suffix"] = suffix
    if suppress_author:
        entry["suppress-author"] = True
    return entry


def citation_field(
    items: list[dict],
    *,
    citation_id: str | None = None,
    formatted: str = "",
    note_index: int = 0,
) -> str:
    """The complete `ADDIN ZOTERO_ITEM` field code.

    More than one entry in `items` makes a grouped citation — one field with
    several citationItems, which is what produces `(1,2)` rather than two
    adjacent fields. That is not an edge case: in the calibration draft 45% of
    the citation parentheses grouped two or more references, one of them seven
    (SPEC §11.2 item 12, §14.2).

    `formatted` is left empty by default. A single Refresh in Word fills it in
    correctly, numbering and grouping included, which is the cheap half of the
    choice in SPEC §7.5 — the Zotero API cannot pre-render a numeric style,
    because it renders each item in isolation and knows nothing of the order
    they appear in the document.
    """
    if not items:
        raise ValueError("a citation with no items is not a citation")
    if note_index:
        raise ValueError(
            "footnote styles are not supported yet and are refused rather than "
            "half-written (SPEC §7.6)"
        )
    obj = {
        "citationID": citation_id or new_citation_id(),
        "properties": {
            "formattedCitation": formatted,
            "plainCitation": formatted,
            "noteIndex": note_index,
        },
        "citationItems": items,
        "schema": SCHEMA,
    }
    return MARK_ITEM + json.dumps(obj, ensure_ascii=False)


def document_prefs(
    style: str,
    locale: str = "en-GB",
    *,
    session_id: str | None = None,
    zotero_version: str = "7.0.29",
) -> list[tuple[str, str]]:
    """The ZOTERO_PREF custom properties, as (name, value) pairs.

    These are not a field: they live in `docProps/custom.xml`, and they are the
    piece the whole promise rests on. Once they are in the document, changing
    the citation style is not catena's job at all — it is Document Preferences
    in Word, verified end to end from Vancouver to APA (SPEC §7.4, §12.3).
    """
    if not style.startswith("http"):
        raise ValueError(f"style must be a CSL style URL, got {style!r}")
    raw = (
        f'<data data-version="3" zotero-version="{zotero_version}">'
        f'<session id="{session_id or new_citation_id()}"/>'
        f'<style id="{style}" locale="{locale}" hasBibliography="1" '
        'bibliographyStyleHasBeenSet="1"/>'
        '<prefs><pref name="fieldType" value="Field"/>'
        '<pref name="dontAskDelayCitationUpdates" value="true"/></prefs>'
        "</data>"
    )
    chunks = [raw[i : i + PREF_CHUNK] for i in range(0, len(raw), PREF_CHUNK)]
    return [(f"ZOTERO_PREF_{n}", c) for n, c in enumerate(chunks, start=1)]


def custom_properties_xml(prefs: list[tuple[str, str]]) -> str:
    """`docProps/custom.xml` for a document whose only properties are Zotero's."""
    props = []
    for n, (name, value) in enumerate(prefs, start=2):  # pid starts at 2
        props.append(
            f'<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="{n}" '
            f"name=\"{name}\"><vt:lpwstr>{escape(value)}</vt:lpwstr></property>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        + "".join(props)
        + "</Properties>"
    )
