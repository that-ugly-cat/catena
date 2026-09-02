#!/usr/bin/env python3
"""
build_fixture.py — builds the test .docx for the spike of SPEC §7.

It assembles by hand the minimal OOXML of a Word document carrying real Zotero
fields, so that Word (with Zotero running) can be asked what happens on Refresh
and on a style change. It deliberately avoids python-docx: the point is to
control the shape of the field byte by byte, and a high-level library would hide
exactly that.

The cases, one per paragraph:

  1. correct uris, id as a string mirroring itemData.id
  2. correct uris, id field ABSENT altogether
  3. a repeat of item 1            -> in Vancouver, the same number as case 1
  4. multiple citation (items 2+3) -> a grouped form like (2,3)
  5. uris pointing at a key that does not exist -> falls back to embedded data

Usage:  uv run build_fixture.py fixture_items.json catena-spike.docx
"""

import json
import sys
import zipfile
from html import escape
from pathlib import Path

SCHEMA = "https://github.com/citation-style-language/schema/raw/master/csl-citation.json"
STYLE = "http://www.zotero.org/styles/vancouver"
LOCALE = "en-GB"
SESSION_ID = "cAtEnA01"

# 8 characters, one per occurrence: in the real manuscript citationIDs are
# unique per occurrence and not per item (SPEC §7.1).
CITATION_IDS = ["spKe0001", "spKe0002", "spKe0003", "spKe0004", "spKe0005"]


def uri(library: str, key: str) -> str:
    return f"http://zotero.org/{library}/items/{key}"


def citation_json(citation_id: str, entries: list[dict]) -> str:
    """entries: [{'item': <fixture item>, 'id': <value or None>, 'uris': [...]}]"""
    items = []
    for e in entries:
        ci: dict = {}
        if e["id"] is not None:
            ci["id"] = e["id"]
        ci["uris"] = e["uris"]
        ci["itemData"] = e["item"]["csl"]
        items.append(ci)
    obj = {
        "citationID": citation_id,
        "properties": {
            # Left empty on purpose: if Refresh fills these in correctly, the
            # pre-rendering of SPEC §7.5 is an optional nicety rather than a
            # requirement.
            "formattedCitation": "",
            "plainCitation": "",
            "noteIndex": 0,
        },
        "citationItems": items,
        "schema": SCHEMA,
    }
    return "ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps(obj, ensure_ascii=False)


def field_runs(instr: str, result_text: str) -> str:
    """A complete Word field: begin / instrText / separate / result / end.

    instrText is split across runs: Word tolerates long ones, but Zotero itself
    splits, and it is worth exercising the same path.
    """
    chunks = [instr[i : i + 1000] for i in range(0, len(instr), 1000)]
    parts = ['<w:r><w:fldChar w:fldCharType="begin"/></w:r>']
    for c in chunks:
        parts.append(
            f'<w:r><w:instrText xml:space="preserve">{escape(c)}</w:instrText></w:r>'
        )
    parts.append('<w:r><w:fldChar w:fldCharType="separate"/></w:r>')
    parts.append(f"<w:r><w:t>{escape(result_text)}</w:t></w:r>")
    parts.append('<w:r><w:fldChar w:fldCharType="end"/></w:r>')
    return "".join(parts)


def para(text_before: str, field_xml: str = "", text_after: str = "") -> str:
    runs = ""
    if text_before:
        runs += f'<w:r><w:t xml:space="preserve">{escape(text_before)}</w:t></w:r>'
    runs += field_xml
    if text_after:
        runs += f'<w:r><w:t xml:space="preserve">{escape(text_after)}</w:t></w:r>'
    return f"<w:p>{runs}</w:p>"


def heading(text: str) -> str:
    return (
        '<w:p><w:pPr><w:spacing w:before="240"/></w:pPr>'
        f'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
    )


def build_document(items: list[dict]) -> str:
    a, b, c = items[0], items[1], items[2]
    body = []

    body.append(heading("catena — spike §7. Open with Zotero running, then Refresh."))
    body.append(
        para(
            "Each paragraph tests one case. After the Refresh, check the number "
            "produced and whether Zotero reports any missing item. Then Document "
            "Preferences -> APA, and check that every case reformats."
        )
    )

    # 1. correct uris + string id
    body.append(heading("1. correct uris, id = string mirroring itemData.id"))
    body.append(
        para(
            "First item, cited normally ",
            field_runs(
                citation_json(
                    CITATION_IDS[0],
                    [{"item": a, "id": a["csl"].get("id"), "uris": [uri(a["library"], a["key"])]}],
                ),
                "[1]",
            ),
            ". Expected: (1).",
        )
    )

    # 2. correct uris, id absent
    body.append(heading("2. correct uris, id field ABSENT"))
    body.append(
        para(
            "Second item, with no id ",
            field_runs(
                citation_json(
                    CITATION_IDS[1],
                    [{"item": b, "id": None, "uris": [uri(b["library"], b["key"])]}],
                ),
                "[2]",
            ),
            ". Expected: (2), and no prompt. If Zotero asks to reselect, the id "
            "is not optional after all.",
        )
    )

    # 3. a repeat of the first
    body.append(heading("3. a repeat of item 1"))
    body.append(
        para(
            "The first item again ",
            field_runs(
                citation_json(
                    CITATION_IDS[2],
                    [{"item": a, "id": a["csl"].get("id"), "uris": [uri(a["library"], a["key"])]}],
                ),
                "[1]",
            ),
            ". Expected: (1), the same number as case 1.",
        )
    )

    # 4. multiple citation
    body.append(heading("4. multiple citation (item 2 + item 3)"))
    body.append(
        para(
            "Two items in a single field ",
            field_runs(
                citation_json(
                    CITATION_IDS[3],
                    [
                        {"item": b, "id": b["csl"].get("id"), "uris": [uri(b["library"], b["key"])]},
                        {"item": c, "id": c["csl"].get("id"), "uris": [uri(c["library"], c["key"])]},
                    ],
                ),
                "[2,3]",
            ),
            ". Expected: (2,3) — grouped, not two separate fields.",
        )
    )

    # 5. non-resolving uri -> fallback to embedded data
    body.append(heading("5. uris that do not resolve, itemData present"))
    body.append(
        para(
            "Item with a broken URI ",
            field_runs(
                citation_json(
                    CITATION_IDS[4],
                    [
                        {
                            "item": c,
                            "id": c["csl"].get("id"),
                            "uris": [uri("groups/6378365", "ZZZZZZZZ")],
                        }
                    ],
                ),
                "[4]",
            ),
            ". Expected: it formats anyway from the embedded data, with no "
            "prompt. If an 'item not found' dialog appears, the fallback needs "
            "something more.",
        )
    )

    body.append(heading("Bibliography"))
    body.append(
        para(
            "",
            field_runs(
                'ADDIN ZOTERO_BIBL {"uncited":[],"omitted":[],"custom":[]} CSL_BIBLIOGRAPHY',
                "[the bibliography appears here after the Refresh]",
            ),
        )
    )

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(body) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>'
        "</w:body></w:document>"
    )


def build_custom_props() -> tuple[str, int]:
    """ZOTERO_PREF_1/_2 as custom properties, split at 255 characters.

    Verified on the real manuscript: Word truncates vt:lpwstr at 255, which is
    why Zotero uses two properties. The limit applies to the value and not to
    its XML serialisation, so the raw string is split first and each piece
    escaped afterwards — never the other way round (SPEC §7.4).
    """
    prefs = (
        '<data data-version="3" zotero-version="7.0.29">'
        f'<session id="{SESSION_ID}"/>'
        f'<style id="{STYLE}" locale="{LOCALE}" hasBibliography="1" '
        'bibliographyStyleHasBeenSet="1"/>'
        '<prefs><pref name="fieldType" value="Field"/>'
        '<pref name="dontAskDelayCitationUpdates" value="true"/></prefs>'
        "</data>"
    )
    chunks = [prefs[i : i + 255] for i in range(0, len(prefs), 255)]
    props = []
    for n, ch in enumerate(chunks, start=1):
        pid = n + 1  # pid starts at 2
        props.append(
            f'<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="{pid}" '
            f'name="ZOTERO_PREF_{n}"><vt:lpwstr>{escape(ch)}</vt:lpwstr></property>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        + "".join(props)
        + "</Properties>"
    ), len(chunks)


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/docProps/custom.xml" ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" Target="docProps/custom.xml"/>
</Relationships>"""


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "fixture_items.json")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "catena-spike.docx")

    items = json.loads(src.read_text(encoding="utf-8"))
    if len(items) < 3:
        print("the input json needs at least 3 items", file=sys.stderr)
        return 1

    document = build_document(items)
    custom, n_chunks = build_custom_props()

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("word/document.xml", document)
        z.writestr("docProps/custom.xml", custom)

    print(f"wrote {out}")
    print("  citation fields : 5")
    print("  bibliography    : 1")
    print(f"  ZOTERO_PREF     : {n_chunks} properties")
    print(f"  style           : {STYLE.rsplit('/', 1)[-1]} ({LOCALE})")
    print(f"  items used      : {', '.join(i['key'] for i in items[:3])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
