"""
Zotero fields inside a Word document: how to read them.

The format was verified against a real manuscript and reproduced in a fixture
that was then opened in Word (SPEC §7 and §12). Three shapes matter:

    ADDIN ZOTERO_ITEM CSL_CITATION {…}                       a citation
    ADDIN ZOTERO_BIBL {…} CSL_BIBLIOGRAPHY                   the bibliography
    docProps/custom.xml -> ZOTERO_PREF_1 / _2                style and prefs

The preferences are not a field: they live in the package's custom properties,
split at 255 **unescaped** characters (SPEC §7.4).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from xml.etree import ElementTree

from .ooxml import CUSTOM, Document

MARK_ITEM = "ADDIN ZOTERO_ITEM CSL_CITATION "
MARK_BIBL = "ADDIN ZOTERO_BIBL "

RE_PREF = re.compile(
    r'name="(ZOTERO_PREF_\d+)"><vt:lpwstr>(.*?)</vt:lpwstr>', re.S
)
RE_DOI = re.compile(r"^10\.\d{4,9}/\S+$")


@dataclass
class CitationItem:
    """One item inside a citation."""

    uris: list[str]
    item_data: dict
    raw_id: object = None

    @property
    def library(self) -> str | None:
        """`groups/123` or `users/456`, taken from the first usable URI."""
        for u in self.uris:
            m = re.search(r"zotero\.org/(users/local/[^/]+|users/\d+|groups/\d+)/", u)
            if m:
                return m.group(1)
        return None

    @property
    def key(self) -> str | None:
        for u in self.uris:
            m = re.search(r"/items/([A-Z0-9]{8})\b", u)
            if m:
                return m.group(1)
        return None

    @property
    def is_local_uri(self) -> bool:
        """A URI tied to a local Zotero profile: it resolves nowhere else (SPEC §7.2)."""
        lib = self.library
        return bool(lib and lib.startswith("users/local/"))

    @property
    def doi(self) -> str | None:
        v = self.item_data.get("DOI")
        return v.strip() if isinstance(v, str) and v.strip() else None

    @property
    def title(self) -> str:
        return (self.item_data.get("title") or "").strip()

    @property
    def year(self) -> str | None:
        issued = self.item_data.get("issued") or {}
        parts = issued.get("date-parts") or []
        if parts and parts[0]:
            return str(parts[0][0])
        return None

    @property
    def first_author(self) -> str | None:
        for a in self.item_data.get("author") or []:
            name = a.get("family") or a.get("literal")
            if name:
                return name.strip()
        return None

    def signature(self) -> str:
        """Bibliographic identity, to spot the same paper under different URIs."""
        if self.doi:
            return "doi:" + self.doi.lower()
        norm = re.sub(r"[^a-z0-9]+", " ", self.title.lower()).strip()
        return f"t:{norm}|{self.year or ''}|{(self.first_author or '').lower()}"


@dataclass
class Citation:
    """One ZOTERO_ITEM field."""

    citation_id: str | None
    formatted: str
    note_index: int
    items: list[CitationItem]
    order: int

    @property
    def is_footnote_style(self) -> bool:
        return bool(self.note_index)


def _decode_after(blob: str, start: int) -> tuple[dict | None, int]:
    """Decode the JSON starting at `start`, returning (object, end)."""
    dec = json.JSONDecoder()
    try:
        obj, end = dec.raw_decode(blob[start:])
        return obj, start + end
    except json.JSONDecodeError:
        return None, start


def parse_citations(doc: Document) -> list[Citation]:
    """Every Zotero citation in the document, in order of appearance."""
    blob = doc.field_codes
    out: list[Citation] = []
    i = 0
    while True:
        i = blob.find(MARK_ITEM, i)
        if i < 0:
            break
        start = i + len(MARK_ITEM)
        obj, _ = _decode_after(blob, start)
        i = start
        if not obj:
            continue
        props = obj.get("properties") or {}
        items = [
            CitationItem(
                uris=list(ci.get("uris") or []),
                item_data=ci.get("itemData") or {},
                raw_id=ci.get("id"),
            )
            for ci in obj.get("citationItems") or []
        ]
        out.append(
            Citation(
                citation_id=obj.get("citationID"),
                formatted=props.get("formattedCitation") or "",
                note_index=props.get("noteIndex") or 0,
                items=items,
                order=len(out),
            )
        )
    return out


def has_bibliography(doc: Document) -> bool:
    return MARK_BIBL in doc.field_codes


@dataclass
class DocumentPrefs:
    style: str | None
    locale: str | None
    field_type: str | None
    chunks: int
    raw: str

    @property
    def style_name(self) -> str | None:
        return self.style.rsplit("/", 1)[-1] if self.style else None


def parse_prefs(doc: Document) -> DocumentPrefs | None:
    """The ZOTERO_PREF value, reassembled from the custom-property chunks."""
    custom = doc.parts.get(CUSTOM)
    if not custom:
        return None
    props = dict(RE_PREF.findall(custom))
    if not props:
        return None
    ordered = sorted(props, key=lambda k: int(k.rsplit("_", 1)[1]))
    raw = "".join(unescape(props[k]) for k in ordered)
    style = locale = field_type = None
    try:
        root = ElementTree.fromstring(raw)
        st = root.find("style")
        if st is not None:
            style = st.get("id")
            locale = st.get("locale")
        for pref in root.iterfind("./prefs/pref"):
            if pref.get("name") == "fieldType":
                field_type = pref.get("value")
    except ElementTree.ParseError:
        pass
    return DocumentPrefs(style, locale, field_type, len(props), raw)


def looks_like_doi(value: str | None) -> bool:
    return bool(value and RE_DOI.match(value.strip()))
