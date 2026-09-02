#!/usr/bin/env python3
"""
check_fixture.py — static checks on a .docx carrying Zotero fields.

Reads back the generated file with the same parser used to analyse the real
manuscript, and checks the invariants that can be established without opening
Word. It does not replace the round trip: it only says the file is well formed
and its fields decode the way Zotero would read them.

    uv run check_fixture.py catena-spike.docx
"""

from __future__ import annotations

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

    print("package structure")
    for part in ("[Content_Types].xml", "_rels/.rels", "word/document.xml", "docProps/custom.xml"):
        check(part, part in names)

    print("\nwell-formed XML")
    for label, blob in (("word/document.xml", doc), ("docProps/custom.xml", custom)):
        try:
            ElementTree.fromstring(blob)
            check(label, True)
        except ElementTree.ParseError as e:
            check(label, False, str(e))

    print("\nWord fields: begin/separate/end structure")
    root = ElementTree.fromstring(doc)
    begins = len(root.findall(f".//{W}fldChar[@{W}fldCharType='begin']"))
    seps = len(root.findall(f".//{W}fldChar[@{W}fldCharType='separate']"))
    ends = len(root.findall(f".//{W}fldChar[@{W}fldCharType='end']"))
    check(f"begin == end ({begins} == {ends})", begins == ends)
    check(f"separate == begin ({seps} == {begins})", seps == begins)

    print("\nZotero fields: JSON decoding")
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
            check(f"citation at offset {s}", False, str(e))
        i = s
    check(f"citations decoded: {len(cits)}", len(cits) == 5, "expected 5")
    check("exactly one ZOTERO_BIBL field", joined.count("ADDIN ZOTERO_BIBL") == 1)

    print("\nper-citation invariants")
    ids = [c.get("citationID") for c in cits]
    check(f"citationIDs unique ({len(set(ids))}/{len(ids)})", len(set(ids)) == len(ids))
    check("citationIDs are 8 characters", all(len(str(x)) == 8 for x in ids))
    check("schema present throughout", all("schema" in c for c in cits))
    check("noteIndex is 0 throughout", all(c["properties"].get("noteIndex") == 0 for c in cits))

    for n, c in enumerate(cits, 1):
        items = c["citationItems"]
        has_uris = all(isinstance(ci.get("uris"), list) and ci["uris"] for ci in items)
        has_data = all(ci.get("itemData") for ci in items)
        check(f"citation {n}: uris is a non-empty list", has_uris)
        check(f"citation {n}: itemData present", has_data)

    print("\nthe spike cases")
    check("case 2 carries no id field", "id" not in cits[1]["citationItems"][0])
    check("case 4 has two citationItems", len(cits[3]["citationItems"]) == 2)
    check(
        "case 5 points at a key that does not exist",
        "ZZZZZZZZ" in cits[4]["citationItems"][0]["uris"][0],
    )
    check(
        "cases 1 and 3 cite the same URI",
        cits[0]["citationItems"][0]["uris"] == cits[2]["citationItems"][0]["uris"],
    )

    print("\nZOTERO_PREF")
    props = dict(
        re.findall(r'name="(ZOTERO_PREF_\d+)"><vt:lpwstr>(.*?)</vt:lpwstr>', custom, re.S)
    )
    check(f"properties found: {len(props)}", len(props) >= 1)
    # Word's limit on vt:lpwstr is on the value, not on its serialisation:
    # measured on the real manuscript, ZOTERO_PREF_1 is 255 unescaped characters
    # and 288 escaped. The unescaped length is the one to check.
    raw = {k: unescape(v) for k, v in props.items()}
    check(
        "no chunk exceeds 255 unescaped characters",
        all(len(v) <= 255 for v in raw.values()),
        f"max {max((len(v) for v in raw.values()), default=0)}",
    )
    reassembled = "".join(raw[k] for k in sorted(raw, key=lambda s: int(s.rsplit("_", 1)[1])))
    try:
        pref = ElementTree.fromstring(reassembled)
        check("the chunks reassemble into valid XML", True)
        style = pref.find("style")
        check("style id present", style is not None and style.get("id", "").startswith("http"))
        check("fieldType is Field", 'name="fieldType" value="Field"' in reassembled)
    except ElementTree.ParseError as e:
        check("the chunks reassemble into valid XML", False, str(e))

    print(f"\n{ok} ok, {fail} fail")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
