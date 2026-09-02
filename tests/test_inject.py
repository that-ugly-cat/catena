"""
Placing fields into a document that already exists — SPEC §8.

The cases that matter are the ones where being wrong is invisible: a marker
split across runs that silently fails to match, a field that lands outside the
revision block it belonged in and so changes who appears to have written the
sentence, a comment anchor cut in half. Each of those produces a file that opens
fine.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from catena.inject import (
    Replacement,
    fresh_citation_id,
    inject,
    paragraph_runs,
    paragraph_text,
    replace_in_paragraph,
)

FIELD = 'ADDIN ZOTERO_ITEM CSL_CITATION {"citationID": "AAAAAAAA", "citationItems": []}'


def run(text: str, props: str = "") -> str:
    return f'<w:r>{props}<w:t xml:space="preserve">{text}</w:t></w:r>'


def para(*runs: str) -> str:
    return "<w:p>" + "".join(runs) + "</w:p>"


def rep(text: str, marker: str) -> Replacement:
    i = text.index(marker)
    return Replacement(i, i + len(marker), marker, FIELD, "[1]")


# --- reading runs -------------------------------------------------------------


def test_w_r_does_not_match_w_rpr():
    """The same trap as `<w:t>`: `<w:rPr>` starts with `<w:r` (SPEC §14.3)."""
    p = para('<w:r><w:rPr><w:b/></w:rPr><w:t>hello</w:t></w:r>')
    assert [r.text for r in paragraph_runs(p)] == ["hello"]


def test_text_is_recomposed_across_runs():
    p = para(run("see ("), run("Becker"), run(" et al., 2022)"), run(" and so on"))
    assert paragraph_text(paragraph_runs(p)) == "see (Becker et al., 2022) and so on"


# --- replacing ----------------------------------------------------------------


def test_a_marker_split_across_five_runs_is_replaced():
    """Word splits runs wherever it likes; the marker has to survive that."""
    p = para(run("as "), run("(Bec"), run("ker et"), run(" al., 2022"), run(") shows"))
    text = paragraph_text(paragraph_runs(p))
    out, skipped = replace_in_paragraph(p, [rep(text, "(Becker et al., 2022)")])
    assert not skipped
    assert "ADDIN ZOTERO_ITEM" in out
    assert "Becker" not in re.sub(r"<w:instrText.*?</w:instrText>", "", out, flags=re.S)
    assert "as " in out and " shows" in out, "the surrounding text survives"


def test_the_field_stays_inside_a_tracked_insertion():
    """Otherwise the citation is attributed to the wrong person (SPEC §8.3)."""
    inner = run("(Becker et al., 2022)")
    p = (
        "<w:p>"
        + run("before ")
        + f'<w:ins w:id="7" w:author="A Coauthor" w:date="2026-09-01T00:00:00Z">{inner}</w:ins>'
        + run(" after</w:t></w:r>".replace("</w:t></w:r>", ""))
        + "</w:p>"
    )
    text = paragraph_text(paragraph_runs(p))
    out, skipped = replace_in_paragraph(p, [rep(text, "(Becker et al., 2022)")])
    assert not skipped
    ins_block = re.search(r"<w:ins\s.*?</w:ins>", out, re.S).group(0)
    assert "ADDIN ZOTERO_ITEM" in ins_block, "the field must sit inside the w:ins"
    assert 'w:author="A Coauthor"' in ins_block


def test_a_marker_straddling_a_comment_anchor_is_left_alone():
    p = para(run("see (Becker")) .replace(
        "</w:p>", '<w:commentRangeStart w:id="1"/>' + run(" et al., 2022) here") + "</w:p>"
    )
    text = paragraph_text(paragraph_runs(p))
    out, skipped = replace_in_paragraph(p, [rep(text, "(Becker et al., 2022)")])
    assert len(skipped) == 1 and "comment anchor" in skipped[0].reason
    assert "ADDIN" not in out, "nothing was placed"
    assert "commentRangeStart" in out, "and the anchor is intact"


def test_run_formatting_is_carried_onto_the_field():
    props = "<w:rPr><w:i/></w:rPr>"
    p = para(run("x ", props), run("(Becker et al., 2022)", props))
    text = paragraph_text(paragraph_runs(p))
    out, _ = replace_in_paragraph(p, [rep(text, "(Becker et al., 2022)")])
    assert out.count("<w:i/>") >= 3, "the field runs keep the surrounding style"


# --- occurrence identity -------------------------------------------------------


def test_each_placement_gets_its_own_citation_id():
    """SPEC §7.1: unique per occurrence, not per item.

    Caught by catena's own audit on catena's own output: one field code reused
    for six occurrences of the same citation gave all six the same id.
    """
    ids = {re.search(r'"citationID": "([^"]+)"', fresh_citation_id(FIELD)).group(1)
           for _ in range(200)}
    assert len(ids) == 200
    assert "AAAAAAAA" not in ids


# --- the whole file ------------------------------------------------------------


def _minimal_docx(path: Path, body: str, custom: str | None = None) -> Path:
    ct = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        if custom is not None:
            z.writestr("docProps/custom.xml", custom)
    return path


def test_inject_never_writes_over_the_input(tmp_path: Path):
    src = _minimal_docx(tmp_path / "a.docx", para(run("x")))
    with pytest.raises(ValueError, match="refusing to write over"):
        inject(src, {"x": FIELD}, out=src)


def test_an_unplaceable_marker_is_reported_and_the_text_untouched(tmp_path: Path):
    src = _minimal_docx(tmp_path / "a.docx", para(run("nothing here")))
    r = inject(src, {"(Nobody, 1999)": FIELD}, out=tmp_path / "b.docx")
    assert r.unmatched == ["(Nobody, 1999)"]
    assert not r.ok


def test_existing_custom_properties_are_kept(tmp_path: Path):
    """A manuscript carries properties we do not own; dropping them is a silent edit."""
    custom = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="ContentTypeId">'
        "<vt:lpwstr>0x0101ABC</vt:lpwstr></property></Properties>"
    )
    src = _minimal_docx(tmp_path / "a.docx", para(run("cite [X] here")), custom=custom)
    out = tmp_path / "b.docx"
    inject(src, {"[X]": FIELD}, out=out, prefs=[("ZOTERO_PREF_1", "<data/>")])
    with zipfile.ZipFile(out) as z:
        got = z.read("docProps/custom.xml").decode()
    assert "ContentTypeId" in got and "0x0101ABC" in got
    assert "ZOTERO_PREF_1" in got
    assert got.count('pid="2"') == 1, "pids stay unique"


def test_content_type_and_relationship_are_added_when_missing(tmp_path: Path):
    src = _minimal_docx(tmp_path / "a.docx", para(run("cite [X]")))
    out = tmp_path / "b.docx"
    inject(src, {"[X]": FIELD}, out=out, prefs=[("ZOTERO_PREF_1", "<data/>")])
    with zipfile.ZipFile(out) as z:
        assert "custom-properties+xml" in z.read("[Content_Types].xml").decode()
        assert "custom-properties" in z.read("_rels/.rels").decode()
