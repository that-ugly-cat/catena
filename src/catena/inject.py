"""
Putting fields into a document that already exists.

This is the half of catena that touches somebody's manuscript, and it is written
to be boring on purpose. It does not know what a DOI is, it holds no credential,
and it never invents a reference: it receives ready-made field codes and puts
them where the markers were (SPEC §1.2). Everything that requires judgement
happened before it ran.

What it has to get right is the OOXML, and the OOXML is hostile in three
specific ways.

**Runs are arbitrary.** Word splits a paragraph into runs wherever it likes — a
spell-check boundary, a stray formatting change — so `[@Rosato2008]` may be
scattered across five of them and has to be recomposed before it can be found,
and split apart again to be replaced.

**Revisions are not decoration.** In the calibration draft one citation sat
inside a `w:ins` block belonging to a named co-author. Splicing at the run's own
position keeps the field inside that block, which is what preserves the
attribution; anything that rebuilds the paragraph loses it, and the loss looks
like the wrong person wrote the sentence.

**Comment anchors are siblings, not containers.** A marker that straddles one
cannot be replaced without risking the anchor, so it is refused and reported —
an unreplaced marker is a nuisance, a broken comment is somebody's review lost.

Deleted text needs no special handling and gets none: it lives in `w:delText`,
which the `w:t` scan does not see, so a marker inside a deletion stays deleted.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from html import escape, unescape
from pathlib import Path

from .build import new_citation_id
from .ooxml import CUSTOM, DOC, RE_PARA

# `"citationID": "XXXXXXXX"`, as json.dumps writes it and as Word carries it.
RE_CITATION_ID = re.compile(r'("citationID"\s*:\s*")([^"]*)(")')

# `<w:r>` and not `<w:rPr>`: the separator after the tag name is mandatory, the
# same trap as `<w:t>` (SPEC §14.3, trap 8).
RE_RUN = re.compile(r"<w:r(?:\s[^>]*)?>.*?</w:r>", re.S)
RE_RUN_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)
RE_RPR = re.compile(r"<w:rPr>.*?</w:rPr>", re.S)
RE_COMMENT_ANCHOR = re.compile(r"<w:commentR(?:eference|angeStart|angeEnd)\b[^>]*/?>")

CONTENT_TYPES = "[Content_Types].xml"
ROOT_RELS = "_rels/.rels"
CUSTOM_CT = (
    '<Override PartName="/docProps/custom.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.custom-properties+xml"/>'
)
CUSTOM_REL = (
    '<Relationship Id="rIdCatenaCustom" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/custom-properties" '
    'Target="docProps/custom.xml"/>'
)
FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"


# --- reading a paragraph ------------------------------------------------------


@dataclass
class Run:
    xml: str
    xml_start: int
    xml_end: int
    text_start: int
    text: str

    @property
    def text_end(self) -> int:
        return self.text_start + len(self.text)


def paragraph_runs(para_xml: str) -> list[Run]:
    """Runs of a paragraph with both their XML span and their text span."""
    runs: list[Run] = []
    cursor = 0
    for m in RE_RUN.finditer(para_xml):
        text = unescape("".join(RE_RUN_TEXT.findall(m.group(0))))
        runs.append(
            Run(
                xml=m.group(0),
                xml_start=m.start(),
                xml_end=m.end(),
                text_start=cursor,
                text=text,
            )
        )
        cursor += len(text)
    return runs


def paragraph_text(runs: list[Run]) -> str:
    return "".join(r.text for r in runs)


def _run_props(run_xml: str) -> str:
    m = RE_RPR.search(run_xml)
    return m.group(0) if m else ""


def _text_run(props: str, text: str) -> str:
    return f'<w:r>{props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def fresh_citation_id(field_code: str) -> str:
    """Give this placement its own citationID.

    The plan holds one field code per marker, but a marker occurs many times —
    `(Becker et al., 2022)` appears six times in the calibration draft. Reusing
    the code verbatim would give all six the same citationID, and those have to
    be unique per *occurrence* rather than per item (SPEC §7.1).

    This is the injector's business and nobody else's: only the thing placing
    the fields knows how many placements there are. It is still not
    bibliographic logic — the identifier means nothing outside the document.
    """
    return RE_CITATION_ID.sub(lambda m: m.group(1) + new_citation_id() + m.group(3),
                              field_code, count=1)


def field_runs(instr: str, visible: str, props: str = "") -> str:
    """A complete Word field, split across runs the way Zotero splits it."""
    parts = [f"<w:r>{props}<w:fldChar w:fldCharType=\"begin\"/></w:r>"]
    for i in range(0, len(instr), 1000):
        chunk = escape(instr[i : i + 1000])
        parts.append(
            f'<w:r>{props}<w:instrText xml:space="preserve">{chunk}</w:instrText></w:r>'
        )
    parts.append(f'<w:r>{props}<w:fldChar w:fldCharType="separate"/></w:r>')
    parts.append(_text_run(props, visible))
    parts.append(f'<w:r>{props}<w:fldChar w:fldCharType="end"/></w:r>')
    return "".join(parts)


# --- replacing inside a paragraph ---------------------------------------------


@dataclass
class Replacement:
    start: int
    end: int
    marker: str
    field_code: str
    visible: str


@dataclass
class Skipped:
    marker: str
    reason: str


def replace_in_paragraph(
    para_xml: str, replacements: list[Replacement]
) -> tuple[str, list[Skipped]]:
    """Splice fields into one paragraph, right to left so offsets stay valid."""
    runs = paragraph_runs(para_xml)
    if not runs:
        return para_xml, [Skipped(r.marker, "paragraph has no runs") for r in replacements]

    out = para_xml
    skipped: list[Skipped] = []
    for rep in sorted(replacements, key=lambda r: r.start, reverse=True):
        touched = [r for r in runs if r.text_start < rep.end and r.text_end > rep.start]
        if not touched:
            skipped.append(Skipped(rep.marker, "marker not found in any run"))
            continue

        first, last = touched[0], touched[-1]
        between = out[first.xml_start : last.xml_end]
        if RE_COMMENT_ANCHOR.search(between):
            # SPEC §8.3: an unreplaced marker is a nuisance; a comment anchor
            # cut in half is somebody's review lost.
            skipped.append(
                Skipped(rep.marker, "marker straddles a comment anchor, left alone")
            )
            continue

        props = _run_props(first.xml)
        head = first.text[: rep.start - first.text_start]
        tail = last.text[rep.end - last.text_start :]

        new = ""
        if head:
            new += _text_run(_run_props(first.xml), head)
        new += field_runs(fresh_citation_id(rep.field_code), rep.visible, props)
        if tail:
            new += _text_run(_run_props(last.xml), tail)

        out = out[: first.xml_start] + new + out[last.xml_end :]

    return out, skipped


# --- the whole document --------------------------------------------------------


@dataclass
class InjectReport:
    path: Path
    out: Path
    placed: list[str] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    prefs_written: int = 0

    @property
    def ok(self) -> bool:
        return not self.skipped and not self.unmatched


def inject(
    path: str | Path,
    fields: dict[str, str],
    *,
    out: str | Path,
    prefs: list[tuple[str, str]] | None = None,
    visible: dict[str, str] | None = None,
) -> InjectReport:
    """Replace each marker with its field code, into a copy.

    `fields` maps the literal marker text — `[@Rosato2008]`, `[BIBLIOGRAPHY]` —
    to the field code that replaces it. Nothing is guessed: a marker with no
    entry is left exactly as the author typed it and reported, because a marker
    catena does not understand is better than a reference it invented.

    Never in place. The input is somebody's manuscript.
    """
    path, out = Path(path), Path(out)
    if path.resolve() == out.resolve():
        raise ValueError("refusing to write over the input: pass a different --out")
    visible = visible or {}
    report = InjectReport(path=path, out=out)

    with zipfile.ZipFile(path) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    if DOC not in parts:
        raise ValueError(f"{path.name}: no {DOC} inside — is this really a .docx?")

    doc = parts[DOC].decode("utf-8")
    found: set[str] = set()
    rebuilt: list[str] = []
    cursor = 0

    for m in RE_PARA.finditer(doc):
        para = m.group(0)
        runs = paragraph_runs(para)
        text = paragraph_text(runs)
        reps: list[Replacement] = []
        for marker, code in fields.items():
            start = 0
            while (i := text.find(marker, start)) >= 0:
                reps.append(
                    Replacement(
                        start=i,
                        end=i + len(marker),
                        marker=marker,
                        field_code=code,
                        visible=visible.get(marker, marker),
                    )
                )
                found.add(marker)
                start = i + len(marker)
        if reps:
            new_para, skipped = replace_in_paragraph(para, reps)
            report.skipped.extend(skipped)
            report.placed.extend(
                r.marker for r in reps if r.marker not in {s.marker for s in skipped}
            )
            rebuilt.append(doc[cursor : m.start()])
            rebuilt.append(new_para)
            cursor = m.end()

    rebuilt.append(doc[cursor:])
    parts[DOC] = "".join(rebuilt).encode("utf-8")
    report.unmatched = [k for k in fields if k not in found]

    if prefs:
        parts[CUSTOM] = _merge_custom(parts.get(CUSTOM), prefs).encode("utf-8")
        parts[CONTENT_TYPES] = _ensure_content_type(parts[CONTENT_TYPES]).encode("utf-8")
        parts[ROOT_RELS] = _ensure_rel(parts[ROOT_RELS]).encode("utf-8")
        report.prefs_written = len(prefs)

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, blob in parts.items():
            z.writestr(name, blob)
    return report


# --- document properties -------------------------------------------------------


def _merge_custom(existing: bytes | None, prefs: list[tuple[str, str]]) -> str:
    """Add the ZOTERO_PREF properties, keeping whatever else was there.

    A real manuscript carries other custom properties — SharePoint content-type
    ids, template markers — and dropping them to write ours would be a silent
    edit to something we do not own.
    """
    names = {n for n, _ in prefs}
    if existing:
        xml = existing.decode("utf-8")
        xml = re.sub(
            r'<property[^>]*name="ZOTERO_PREF_\d+"[^>]*>.*?</property>', "", xml, flags=re.S
        )
        kept = re.findall(r"<property\b.*?</property>", xml, re.S)
    else:
        kept = []

    # pid must be unique and start at 2; renumber everything we emit.
    props = []
    for n, body in enumerate(kept, start=2):
        props.append(re.sub(r'pid="\d+"', f'pid="{n}"', body, count=1))
    for n, (name, value) in enumerate(prefs, start=2 + len(kept)):
        props.append(
            f'<property fmtid="{FMTID}" pid="{n}" name="{name}">'
            f"<vt:lpwstr>{escape(value)}</vt:lpwstr></property>"
        )
    assert names  # the caller asked for prefs
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        + "".join(props)
        + "</Properties>"
    )


def _ensure_content_type(blob: bytes) -> str:
    xml = blob.decode("utf-8")
    if "docProps/custom.xml" in xml:
        return xml
    return xml.replace("</Types>", CUSTOM_CT + "</Types>")


def _ensure_rel(blob: bytes) -> str:
    xml = blob.decode("utf-8")
    if "custom-properties" in xml:
        return xml
    return xml.replace("</Relationships>", CUSTOM_REL + "</Relationships>")


def copy_untouched(path: str | Path, out: str | Path) -> None:
    """A byte copy, for the paths where nothing needed changing."""
    shutil.copyfile(path, out)
