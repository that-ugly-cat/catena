#!/usr/bin/env python3
"""
check_fixture.py — verifica statica di un .docx con campi Zotero.

Rilegge il file generato con lo stesso parser usato per analizzare il
manoscritto reale, e controlla le invarianti che si possono verificare senza
aprire Word. Non sostituisce il round-trip: dice solo che il file e' ben
formato e che i campi sono decodificabili come li leggerebbe Zotero.

Uso:  uv run check_fixture.py catena-spike.docx
"""

import json
import re
import sys
import zipfile
from html import unescape
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

ok = 0
fail = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok    {label}")
    else:
        fail += 1
        print(f"  FAIL  {label}" + (f" -- {detail}" if detail else ""))


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "catena-spike.docx"

    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        doc = z.read("word/document.xml").decode("utf-8")
        custom = z.read("docProps/custom.xml").decode("utf-8")

    print(f"{path}\n")

    print("struttura del pacchetto")
    for part in ("[Content_Types].xml", "_rels/.rels", "word/document.xml", "docProps/custom.xml"):
        check(part, part in names)

    print("\nXML ben formato")
    for label, blob in (("word/document.xml", doc), ("docProps/custom.xml", custom)):
        try:
            ElementTree.fromstring(blob)
            check(label, True)
        except ElementTree.ParseError as e:
            check(label, False, str(e))

    print("\ncampi Word: struttura begin/separate/end")
    root = ElementTree.fromstring(doc)
    begins = len(root.findall(f".//{W}fldChar[@{W}fldCharType='begin']"))
    seps = len(root.findall(f".//{W}fldChar[@{W}fldCharType='separate']"))
    ends = len(root.findall(f".//{W}fldChar[@{W}fldCharType='end']"))
    check(f"begin == end ({begins} == {ends})", begins == ends)
    check(f"separate == begin ({seps} == {begins})", seps == begins)

    print("\ncampi Zotero: decodifica del JSON")
    joined = "".join(t.text or "" for t in root.findall(f".//{W}instrText"))
    dec = json.JSONDecoder()
    cits = []
    i = 0
    marker = "ADDIN ZOTERO_ITEM CSL_CITATION "
    while True:
        i = joined.find(marker, i)
        if i < 0:
            break
        s = i + len(marker)
        try:
            obj, _ = dec.raw_decode(joined[s:])
            cits.append(obj)
        except json.JSONDecodeError as e:
            check(f"citazione a offset {s}", False, str(e))
        i = s
    check(f"citazioni decodificate: {len(cits)}", len(cits) == 5, "attese 5")
    check("un campo ZOTERO_BIBL", joined.count("ADDIN ZOTERO_BIBL") == 1)

    print("\ninvarianti per citazione")
    ids = [c.get("citationID") for c in cits]
    check(f"citationID unici ({len(set(ids))}/{len(ids)})", len(set(ids)) == len(ids))
    check("citationID di 8 caratteri", all(len(str(x)) == 8 for x in ids))
    check("schema presente ovunque", all("schema" in c for c in cits))
    check("noteIndex = 0 ovunque", all(c["properties"].get("noteIndex") == 0 for c in cits))

    for n, c in enumerate(cits, 1):
        items = c["citationItems"]
        has_uris = all(isinstance(ci.get("uris"), list) and ci["uris"] for ci in items)
        has_data = all(ci.get("itemData") for ci in items)
        check(f"citazione {n}: uris e' una lista non vuota", has_uris)
        check(f"citazione {n}: itemData presente", has_data)

    print("\ncasi dello spike")
    check("caso 2 non ha il campo id", "id" not in cits[1]["citationItems"][0])
    check("caso 4 ha due citationItems", len(cits[3]["citationItems"]) == 2)
    check(
        "caso 5 punta a una key inesistente",
        "ZZZZZZZZ" in cits[4]["citationItems"][0]["uris"][0],
    )
    check(
        "caso 1 e caso 3 citano lo stesso URI",
        cits[0]["citationItems"][0]["uris"] == cits[2]["citationItems"][0]["uris"],
    )

    print("\nZOTERO_PREF")
    props = dict(
        re.findall(
            r'name="(ZOTERO_PREF_\d+)"><vt:lpwstr>(.*?)</vt:lpwstr>', custom, re.S
        )
    )
    check(f"proprieta' trovate: {len(props)}", len(props) >= 1)
    # Il limite di Word su vt:lpwstr e' sul valore, non sulla sua
    # serializzazione: misurato sul manoscritto reale, ZOTERO_PREF_1 e' 255
    # caratteri non escapati e 288 escapati. Si controlla il non escapato.
    raw = {k: unescape(v) for k, v in props.items()}
    check(
        "nessun chunk supera 255 caratteri non escapati",
        all(len(v) <= 255 for v in raw.values()),
        f"max {max((len(v) for v in raw.values()), default=0)}",
    )
    reassembled = "".join(raw[k] for k in sorted(raw, key=lambda s: int(s.rsplit("_", 1)[1])))
    try:
        pref = ElementTree.fromstring(reassembled)
        check("i chunk si ricompongono in XML valido", True)
        style = pref.find("style")
        check("style id presente", style is not None and style.get("id", "").startswith("http"))
        check("fieldType = Field", 'name="fieldType" value="Field"' in reassembled)
    except ElementTree.ParseError as e:
        check("i chunk si ricompongono in XML valido", False, str(e))

    print(f"\n{ok} ok, {fail} fail")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
