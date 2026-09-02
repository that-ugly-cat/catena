#!/usr/bin/env python3
"""
build_fixture.py — genera il .docx di prova per lo spike §7 dello SPEC.

Costruisce a mano l'OOXML minimo di un documento Word contenente campi Zotero
veri, per verificare in Word (con Zotero attivo) che cosa succede al Refresh e
al cambio di stile. Non usa python-docx: il punto è controllare byte per byte
la forma del campo, e una libreria di alto livello la nasconderebbe.

Casi coperti, uno per paragrafo:

  1. uris corretti, id stringa che rispecchia itemData.id
  2. uris corretti, campo id ASSENTE del tutto
  3. ripetizione dell'item 1        -> in Vancouver deve tornare lo stesso numero
  4. citazione multipla (item 2+3)  -> deve produrre un raggruppamento tipo (2,3)
  5. uris che puntano a una key inesistente -> deve ricadere su itemData incorporato

Uso:  uv run build_fixture.py fixture_items.json catena-spike.docx
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

# 8 caratteri, uno per occorrenza: nel manoscritto reale i citationID sono
# unici per occorrenza e non per item (SPEC §7.1).
CITATION_IDS = ["spKe0001", "spKe0002", "spKe0003", "spKe0004", "spKe0005"]


def uri(library: str, key: str) -> str:
    return f"http://zotero.org/{library}/items/{key}"


def citation_json(citation_id: str, entries: list[dict]) -> str:
    """entries: [{'item': <fixture item>, 'id': <valore o None>, 'uris': [...]}]"""
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
            # lasciati vuoti di proposito: se il Refresh li riempie
            # correttamente, il pre-render della SPEC §7.5 e' opzionale
            "formattedCitation": "",
            "plainCitation": "",
            "noteIndex": 0,
        },
        "citationItems": items,
        "schema": SCHEMA,
    }
    return "ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps(obj, ensure_ascii=False)


def field_runs(instr: str, result_text: str) -> str:
    """Un campo Word completo: begin / instrText / separate / risultato / end.

    instrText va spezzato: Word tollera runs lunghi, ma Zotero stesso spezza e
    conviene esercitare lo stesso percorso.
    """
    chunks = [instr[i : i + 1000] for i in range(0, len(instr), 1000)]
    parts = [
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>',
    ]
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

    body.append(heading("catena — spike §7. Aprire con Zotero attivo, poi Refresh."))
    body.append(
        para(
            "Ogni paragrafo prova un caso. Dopo il Refresh, controllare il numero "
            "prodotto e se Zotero segnala item mancanti. Poi Document Preferences "
            "-> APA, e verificare che tutti i casi si riformattino."
        )
    )

    # 1. uris corretti + id stringa
    body.append(heading("1. uris corretti, id = stringa che rispecchia itemData.id"))
    body.append(
        para(
            "Primo item citato normalmente ",
            field_runs(
                citation_json(
                    CITATION_IDS[0],
                    [{"item": a, "id": a["csl"].get("id"), "uris": [uri(a["library"], a["key"])]}],
                ),
                "[1]",
            ),
            ". Atteso: (1).",
        )
    )

    # 2. uris corretti, id assente
    body.append(heading("2. uris corretti, campo id ASSENTE"))
    body.append(
        para(
            "Secondo item senza id ",
            field_runs(
                citation_json(
                    CITATION_IDS[1],
                    [{"item": b, "id": None, "uris": [uri(b["library"], b["key"])]}],
                ),
                "[2]",
            ),
            ". Atteso: (2), nessun prompt. Se Zotero chiede di riselezionare, "
            "l'id non e' opzionale.",
        )
    )

    # 3. ripetizione del primo
    body.append(heading("3. ripetizione dell'item 1"))
    body.append(
        para(
            "Di nuovo il primo item ",
            field_runs(
                citation_json(
                    CITATION_IDS[2],
                    [{"item": a, "id": a["csl"].get("id"), "uris": [uri(a["library"], a["key"])]}],
                ),
                "[1]",
            ),
            ". Atteso: (1), lo stesso numero del caso 1.",
        )
    )

    # 4. citazione multipla
    body.append(heading("4. citazione multipla (item 2 + item 3)"))
    body.append(
        para(
            "Due item in un campo solo ",
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
            ". Atteso: (2,3) — raggruppati, non due campi separati.",
        )
    )

    # 5. uri inesistente -> fallback su itemData
    body.append(heading("5. uris che non risolvono, itemData presente"))
    body.append(
        para(
            "Item con URI rotto ",
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
            ". Atteso: si formatta lo stesso usando i dati incorporati, senza "
            "prompt. Se compare una finestra 'item non trovato', il fallback "
            "richiede qualcosa in piu'.",
        )
    )

    body.append(heading("Bibliografia"))
    body.append(
        para(
            "",
            field_runs(
                'ADDIN ZOTERO_BIBL {"uncited":[],"omitted":[],"custom":[]} CSL_BIBLIOGRAPHY',
                "[la bibliografia compare qui dopo il Refresh]",
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


def build_custom_props() -> str:
    """ZOTERO_PREF_1/_2 come proprieta' custom, spezzate a 255 caratteri.

    Verificato sul manoscritto reale: Word tronca vt:lpwstr a 255, ed e' il
    motivo per cui Zotero usa due proprieta'.
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
        pid = n + 1  # pid parte da 2
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
        print("servono almeno 3 item nel json di input", file=sys.stderr)
        return 1

    document = build_document(items)
    custom, n_chunks = build_custom_props()

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("word/document.xml", document)
        z.writestr("docProps/custom.xml", custom)

    print(f"scritto {out}")
    print(f"  campi citazione : 5")
    print(f"  campo bibl      : 1")
    print(f"  ZOTERO_PREF     : {n_chunks} proprieta'")
    print(f"  stile           : {STYLE.rsplit('/', 1)[-1]} ({LOCALE})")
    print(f"  item usati      : {', '.join(i['key'] for i in items[:3])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
