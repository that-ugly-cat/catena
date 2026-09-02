# spike §7 — the Word round trip

The thing catena needs and no script can establish on its own: whether a `.docx`
with Zotero fields **we generated** behaves like one Zotero generated.

## Rebuilding

```bash
uv run build_fixture.py fixture_items.json catena-spike.docx
uv run check_fixture.py catena-spike.docx     # 33 static checks
```

`fixture_items.json` holds three real items from a Zotero group library, with
their CSL-JSON. They are published papers, so the metadata is public, and a
group id is not a credential — it grants nothing on its own. To use different
items, regenerate the file from the API with `include=csljson,data`.

## The test, in Word

Open `catena-spike.docx` with Zotero running, press **Refresh**, and read the
five paragraphs.

| # | Case | Expected |
|---|---|---|
| 1 | correct `uris`, string `id` | `(1)` |
| 2 | correct `uris`, **`id` absent** | `(2)`, no prompt |
| 3 | a repeat of item 1 | `(1)` again |
| 4 | two items in a single field | `(2,3)`, grouped |
| 5 | `uris` pointing at a key that does not exist | formats from the embedded data |

Then **Document Preferences → APA** and Refresh again: all five must reformat to
author-date, and the bibliography must follow.

Before the Refresh the citations show `[1]`, `[2]`… written by hand: the
`formattedCitation` values are deliberately empty, so you can see how ugly a
document without pre-rendering would be (SPEC §11.1, item 9).

## Result, 2 September 2026

| # | Vancouver | APA | |
|---|---|---|---|
| 1 | `(1)` | `(Assan et al., 2019)` | ✓ |
| 2 | `(2)`, no prompt | `(Bonham et al., 2009)` | ✓ |
| 3 | `(1)` | `(Assan et al., 2019)` | ✓ |
| 4 | `(2,3)` | `(Bonham et al., 2009; Rosato et al., 2008a)` | ✓ |
| 5 | `(4)` + a duplicate entry | `(Rosato et al., 2008b)` | ✗ |

Changing the style from Word works on a document Zotero never wrote: that is the
proof of the original requirement.

Case 5 fails as described below. In APA the defect is more visible — the
disambiguation marks the same paper `2008a` and `2008b`.

## What the outcomes mean

Case **2** matters most. Zotero's source says that with `uris` present the `id`
field is never read (SPEC §7.7), so omitting it should be harmless. If Word
opened a reselect dialog instead, §7.7 would be wrong and would need rewriting.

Case **5** decides whether the staging of §3.2 is workable: if a URI that does
not resolve produced a prompt rather than falling back to the embedded data,
depositing into a group co-authors cannot see would be unusable.

Case **4** decides whether multiple citations are a matter of marker syntax or a
real problem (SPEC §11.2, item 12).
