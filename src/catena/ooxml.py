"""
Lettura di un .docx a livello OOXML.

Questo modulo non sa niente di Zotero: apre il pacchetto, ne estrae le parti che
contano, e dà accesso al testo e ai campi Word senza interpretarli. Chi
interpreta e' `fields.py`.

Una nota che vale un'ora di lavoro a chi non l'ha ancora persa (SPEC §14.3,
trappola 8): il pattern ingenuo `<w:t[^>]*>` intercetta anche `<w:tcPr>`,
`<w:tab/>` e ogni altro tag che comincia per `w:t`, e restituisce frammenti di
XML al posto del testo. Il separatore dopo `w:t` non e' opzionale.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

# --- pattern OOXML -----------------------------------------------------------

# Il gruppo (?:\s[^>]*)? impone che dopo "w:t" ci sia uno spazio o la chiusura:
# esclude w:tab, w:tc, w:tcPr, w:tbl e compagnia.
RE_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)
RE_INSTR = re.compile(r"<w:instrText(?:\s[^>]*)?>(.*?)</w:instrText>", re.S)
RE_PARA = re.compile(r"<w:p(?:\s[^>]*)?>.*?</w:p>", re.S)
RE_INS = re.compile(r"<w:ins\s[^>]*>.*?</w:ins>", re.S)
RE_DEL = re.compile(r"<w:del\s[^>]*>.*?</w:del>", re.S)
RE_AUTHOR = re.compile(r'w:author="([^"]*)"')
RE_COMMENT_REF = re.compile(r"<w:commentR(?:eference|angeStart|angeEnd)\b")

DOC = "word/document.xml"
CUSTOM = "docProps/custom.xml"
FOOTNOTES = "word/footnotes.xml"
ENDNOTES = "word/endnotes.xml"
COMMENTS = "word/comments.xml"


def xml_text(fragment: str) -> str:
    """Testo visibile di un frammento OOXML, entita' XML risolte."""
    return unescape("".join(RE_TEXT.findall(fragment)))


def instr_text(fragment: str) -> str:
    """Concatenazione dei field code di un frammento.

    Zotero spezza i campi lunghi su piu' run: vanno ricomposti prima di essere
    interpretati (SPEC §7.1).
    """
    return unescape("".join(RE_INSTR.findall(fragment)))


@dataclass
class Paragraph:
    index: int
    xml: str
    text: str
    in_revision: bool = False
    revision_authors: tuple[str, ...] = ()
    has_comment_anchor: bool = False


@dataclass
class Document:
    """Un .docx aperto in sola lettura."""

    path: Path
    parts: dict[str, str] = field(repr=False, default_factory=dict)
    paragraphs: list[Paragraph] = field(repr=False, default_factory=list)

    # -- apertura ------------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path) -> Document:
        path = Path(path)
        parts: dict[str, str] = {}
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            for part in (DOC, CUSTOM, FOOTNOTES, ENDNOTES, COMMENTS):
                if part in names:
                    parts[part] = z.read(part).decode("utf-8", errors="replace")
        if DOC not in parts:
            raise ValueError(f"{path.name}: non contiene {DOC} — e' davvero un .docx?")

        doc = cls(path=path, parts=parts)
        doc.paragraphs = doc._read_paragraphs()
        return doc

    def _read_paragraphs(self) -> list[Paragraph]:
        body = self.parts[DOC]
        out: list[Paragraph] = []
        for i, m in enumerate(RE_PARA.finditer(body)):
            xml = m.group(0)
            # il testo di un paragrafo esclude i field code: quelli non si vedono
            visible = xml_text(RE_INSTR.sub("", xml))
            ins = RE_INS.findall(xml)
            dels = RE_DEL.findall(xml)
            authors = tuple(
                sorted({a for seg in ins + dels for a in RE_AUTHOR.findall(seg)})
            )
            out.append(
                Paragraph(
                    index=i,
                    xml=xml,
                    text=visible,
                    in_revision=bool(ins or dels),
                    revision_authors=authors,
                    has_comment_anchor=bool(RE_COMMENT_REF.search(xml)),
                )
            )
        return out

    # -- viste ---------------------------------------------------------------

    @property
    def text(self) -> str:
        """Testo visibile del corpo, un paragrafo per riga."""
        return "\n".join(p.text for p in self.paragraphs)

    @property
    def field_codes(self) -> str:
        """Tutti i field code del corpo, concatenati."""
        return instr_text(self.parts[DOC])

    @property
    def revision_authors(self) -> list[str]:
        return sorted({a for p in self.paragraphs for a in p.revision_authors})

    @property
    def has_zotero_fields(self) -> bool:
        return "ADDIN ZOTERO_ITEM" in self.field_codes

    def counts(self) -> dict[str, int]:
        body = self.parts[DOC]
        return {
            "paragrafi": len(self.paragraphs),
            "caratteri": len(self.text),
            "campi_zotero": self.field_codes.count("ADDIN ZOTERO_ITEM"),
            "campi_bibliografia": self.field_codes.count("ADDIN ZOTERO_BIBL"),
            "revisioni_inserite": len(RE_INS.findall(body)),
            "revisioni_cancellate": len(RE_DEL.findall(body)),
            "commenti": self.parts.get(COMMENTS, "").count("<w:comment "),
            "note_a_pie": self.parts.get(FOOTNOTES, "").count("<w:footnote "),
        }
