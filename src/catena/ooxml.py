"""
Reading a .docx at the OOXML level.

This module knows nothing about Zotero: it opens the package, pulls out the
parts that matter, and gives access to text and Word fields without
interpreting them. Interpreting is `fields.py`'s job.

One note that is worth an hour to whoever has not yet lost it (SPEC §14.3,
trap 8): the naive pattern `<w:t[^>]*>` also matches `<w:tcPr>`, `<w:tab/>` and
every other tag starting with `w:t`, and hands back fragments of XML instead of
text. The separator after `w:t` is not optional.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

# --- OOXML patterns ----------------------------------------------------------

# The (?:\s[^>]*)? group forces a space or the closing bracket right after
# "w:t", which rules out w:tab, w:tc, w:tcPr, w:tbl and friends.
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
    """The visible text of an OOXML fragment, XML entities resolved."""
    return unescape("".join(RE_TEXT.findall(fragment)))


def instr_text(fragment: str) -> str:
    """The field codes of a fragment, concatenated.

    Zotero splits long fields across several runs, so they have to be put back
    together before they can be read (SPEC §7.1).
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
    """A .docx opened read-only."""

    path: Path
    parts: dict[str, str] = field(repr=False, default_factory=dict)
    paragraphs: list[Paragraph] = field(repr=False, default_factory=list)

    # -- opening -------------------------------------------------------------

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
            raise ValueError(f"{path.name}: no {DOC} inside — is this really a .docx?")

        doc = cls(path=path, parts=parts)
        doc.paragraphs = doc._read_paragraphs()
        return doc

    def _read_paragraphs(self) -> list[Paragraph]:
        body = self.parts[DOC]
        out: list[Paragraph] = []
        for i, m in enumerate(RE_PARA.finditer(body)):
            xml = m.group(0)
            # A paragraph's text excludes its field codes: those are not visible.
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

    # -- views ---------------------------------------------------------------

    @property
    def text(self) -> str:
        """The visible text of the body, one paragraph per line."""
        return "\n".join(p.text for p in self.paragraphs)

    @property
    def field_codes(self) -> str:
        """Every field code in the body, concatenated."""
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
            "paragraphs": len(self.paragraphs),
            "characters": len(self.text),
            "zotero_fields": self.field_codes.count("ADDIN ZOTERO_ITEM"),
            "bibliography_fields": self.field_codes.count("ADDIN ZOTERO_BIBL"),
            "insertions": len(RE_INS.findall(body)),
            "deletions": len(RE_DEL.findall(body)),
            "comments": self.parts.get(COMMENTS, "").count("<w:comment "),
            "footnotes": self.parts.get(FOOTNOTES, "").count("<w:footnote "),
        }
