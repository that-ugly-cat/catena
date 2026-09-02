"""
I campi Zotero dentro un documento Word: come si leggono.

Formato verificato su un manoscritto reale e riprodotto in un fixture provato in
Word (SPEC §7 e §12). Le tre forme che ci interessano:

    ADDIN ZOTERO_ITEM CSL_CITATION {…}                       una citazione
    ADDIN ZOTERO_BIBL {…} CSL_BIBLIOGRAPHY                    la bibliografia
    docProps/custom.xml -> ZOTERO_PREF_1 / _2                 stile e preferenze

Le preferenze non sono un campo: stanno nelle proprieta' custom del pacchetto,
spezzate a 255 caratteri **non escapati** (SPEC §7.4).
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
    """Un item dentro una citazione."""

    uris: list[str]
    item_data: dict
    raw_id: object = None

    @property
    def library(self) -> str | None:
        """`groups/123` o `users/456` estratto dal primo URI utile."""
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
        """URI legato a un profilo Zotero locale: non risolve altrove (SPEC §7.2)."""
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
        """Identita' bibliografica, per riconoscere lo stesso paper sotto URI diversi."""
        if self.doi:
            return "doi:" + self.doi.lower()
        norm = re.sub(r"[^a-z0-9]+", " ", self.title.lower()).strip()
        return f"t:{norm}|{self.year or ''}|{(self.first_author or '').lower()}"


@dataclass
class Citation:
    """Un campo ZOTERO_ITEM."""

    citation_id: str | None
    formatted: str
    note_index: int
    items: list[CitationItem]
    order: int

    @property
    def is_footnote_style(self) -> bool:
        return bool(self.note_index)


def _decode_after(blob: str, start: int) -> tuple[dict | None, int]:
    """Decodifica il JSON che comincia a `start`, restituendo (oggetto, fine)."""
    dec = json.JSONDecoder()
    try:
        obj, end = dec.raw_decode(blob[start:])
        return obj, start + end
    except json.JSONDecodeError:
        return None, start


def parse_citations(doc: Document) -> list[Citation]:
    """Tutte le citazioni Zotero del documento, in ordine di comparsa."""
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
    """Le ZOTERO_PREF ricomposte dai chunk delle proprieta' custom."""
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
