# catena — User Guide

catena puts Zotero references inside Word documents. It ties one paper to one Zotero collection, resolves identifiers into real metadata, and hands back the **field codes** a `.docx` needs — the same fields the Zotero Word plugin writes. It is not a bibliography manager (that is Zotero), not a literature search engine, and it does not write prose: it inserts references, and the text stays yours.

Two halves, and the split matters for what you have to do. The **server** at [catena.borant.eu](https://catena.borant.eu) talks to Zotero and produces field codes as strings. The **injector** runs on your own machine, where the file is, and splices those strings into a copy of your document — so your manuscript never travels to the server, and the injector holds no credential and knows nothing about DOIs.

---

## 1. Getting in

1. Sign in through **Borant ID** (`id.borant.eu`), the single sign-in shared by all the borant tools. Registration is open, but a new account carries no access to anything: you then **request access to catena**, and Spit grants it — an account is the right to ask, not the right to enter.
2. The first time you arrive through the gate, catena creates your profile by itself. There is nothing to fill in, and no password here: the session belongs to the gate, so signing out happens there too.
3. A fresh profile has **no Zotero key**, and catena has no credentials of its own — so at that point it reaches no library at all. The bindings page says so, and sends you to your profile.

The public page at `/` describes the tool and never looks at who is reading it; the app is behind the gate at `/app`. The web app itself is deliberately thin — a profile and a list of bindings. The work happens in a conversation (see §9) and on your own machine (§6–§8).

## 2. The Zotero key, and its perimeter

Create a key at [zotero.org/settings/keys](https://www.zotero.org/settings/keys) and paste it into **Profile**. catena uses your key and reaches exactly as far as you do: a group your key does not see answers *not found*, never *forbidden*.

A key is not acceptable merely because it works, and the profile page validates the **perimeter** before saving anything. The shape it accepts:

```
Personal Library  : library access, NO write, NO files
All Groups        : Read Only
<deposit group>   : Read/Write   ← the only exception
```

The order is the point: the default denies and the exception is a single explicit one. Three verdicts come back from Zotero's own key endpoint:

| Verdict | Meaning |
|---|---|
| `ok` | The default denies, one group is writable. Saved. |
| `wide` | Write access on your personal library, or **All Groups** set to Read/Write. **Refused, nothing is saved.** |
| `narrow` | No writable group at all, so catena has nowhere to deposit. Refused. |

`wide` is refused because of a case that happens in real life: with **All Groups** on Read/Write, the `write: false` you see on today's groups are exceptions to a permissive default, so **every group you create or join from then on is born writable** — a perimeter that grows on its own and that nobody remembers six months later.

**Forgetting a key here does not revoke it.** Revocation happens on zotero.org, and that is the one that counts if the key has leaked.

## 3. Bindings: one paper, one collection

A **binding** is the object that makes everything else unambiguous: it ties one paper to one Zotero collection, so a citation request never has to guess which library to look in. It has two legs, and they are usually two different places:

- the **source leg** — the library and collection where that paper's references already live, typically a co-authored group library your key can only read;
- the **deposit leg** — a library your key can write to, where new items land.

The two are separate because the alternative is to widen your key to write on the co-authors' group for the duration of the work, and **a temporary perimeter is a perimeter somebody forgets to close**. When the source and the deposit library are the same, the binding is shown as **single leg** and only one collection is read.

A binding also carries the **citation style** (default Vancouver) and the **locale** (default `en-GB`), which is what §7 writes into the document properties.

Bindings are created from the conversation with `create_binding`; the bindings page shows them and does not make them. The deposit collection has to exist first — `create_collection` makes it, so that creating a collection is always a separate, visible act. There is **no tool that edits or deletes a binding**: a binding with the wrong label or the wrong style is replaced by making another one.

## 4. Collections and items

`list_libraries` reports every library your key reaches, with group names and whether each is writable — read it before creating a binding, since the deposit library must be one of the writable ones and there is usually exactly one. `list_collections` lists a library's collections; `collection_items` reads a binding's collections with both legs merged.

Two behaviours worth knowing before you cite anything:

- `collection_items` **flags anything that appears on both legs**. The same paper reached through two different URIs produces two bibliography entries and two numbers — in an author-date style, a `2008a`/`2008b` that does not exist. When that happens, cite the source-leg copy, and `citation_field` prefers it for you: keys are resolved on the source leg first, so a paper that exists in both is cited from the copy co-authors can actually resolve.
- `search_library` is **lexical, not semantic**: no hit means those words are not in the title, creators or indexed full text, never that the paper is absent from your library.

## 5. Adding references

Every identifier goes up a ladder — DOI, arXiv, ISBN, URL, PMID — and metadata comes from **Zotero's own translators**, the ones behind the connector button, or it does not come at all. catena will not build an item from a title, because an item assembled from memory looks exactly like a real one, which makes it the only error here that nobody can see. The URL rung matters more than it looks: measured on a real library of some 16,000 citable items, DOIs cover 93% of journal articles and roughly one citable item in five has no DOI at all.

- `resolve_identifier` previews what one identifier turns into. It writes nothing.
- `plan_ingest` resolves a batch and **decides nothing**. Each row comes back as `new`, `present` (already in the collection), `ambiguous` (same title, no shared identifier) or `unresolved`, with the warnings that should give you pause. One confirmation on a plan you can read whole beats forty blind ones, which is what a per-item confirmation becomes on a real draft.
- `apply_ingest` executes **exactly that plan, once**. A plan already applied is refused rather than replayed, and every write is guarded by a unique row on (binding, identifier), so a retried call recognises what it already did instead of adding a second copy. `ambiguous` rows are skipped unless you explicitly force them — say yes to that only after a person has looked.
- `add_item` is a plan of one, for a single reference. `add_verified` adds a paper **together with the reason it is cited**: verbatim passages from a verification arrive as a child note on the Zotero item, plus a tag carrying the bearing (`catena:supports`, `catena:contradicts`, `catena:qualifies`). A paper that contradicts you belongs in the bibliography — that is half the point of verifying — and the tag is there so that it is never cited as though it agreed.

Two checks run inside the plan and are worth reading rather than skipping. The **author cross-check**: if your draft attaches a surname to an identifier, that surname resolves nothing but checks everything — a DOI that comes back with someone else's paper was copied wrong, and the plan is the cheapest place in the system to notice. And the **item type warning**: CSL formats by type, so a report filed as a webpage comes out wrong in every style; translators are least reliable on `document`, `webpage`, `report`, `manuscript` and `presentation`, so those are put in front of you instead of trusted.

## 6. Reading a draft that already has citations

Run this on your own machine, on the file itself. It is read-only, needs no network and no credentials:

```
python -m catena.cli discover draft.docx
```

`discover` reads the citations as the author typed them — `(Rosato et al., 2008)`, `(Bullo & Hearn, 2024; Hudson, 2021)` — finds the hand-made reference list at the end, and attaches each in-text citation to its entry. It reports how many carry a DOI, ISBN or URL and can therefore be resolved, how many have no identifier and need a person, and which citations matched no entry at all. It resolves nothing by itself: a citation it cannot read is one somebody looks at, never one catena guesses.

`python -m catena.cli audit manuscript.docx` is the other read-only pass, for a document that already contains Zotero fields. It reports duplicate surrogates (the defect above, and the nastiest of the lot because the document looks correct), URIs tied to a local Zotero profile — those resolve on exactly one machine in the world and are orphans to every co-author — items with no URI or no embedded data, DOIs that are not DOIs, missing bibliography, footnote styles, and tracked changes that an injection has to preserve. It exits `0` clean, `1` with at least one error, `2` on a file it cannot read. Worth running on any manuscript you inherit, before you touch it.

## 7. Field codes, markers, and the injector

A Zotero citation in Word is a **field code**: an instruction Word carries invisibly, holding the item's CSL-JSON and a URI that relinks it to the library item. `citation_field` returns one, ready to place. Several keys in one call make a **grouped** citation — one field, several items, which renders as `(1,2)` rather than as two adjacent fields, and that is the normal case rather than the exception. `bibliography_field` returns the one field that marks where the reference list goes. `document_prefs` returns the two `ZOTERO_PREF` custom properties that make the file a Zotero document — the piece that lets you change citation style from Word afterwards.

You place them by typing a **marker** in the draft where the citation belongs — any literal text you choose, by convention something like `[@Rosato2008]` and `[BIBLIOGRAPHY]` — and then running the injector with a plan that maps each marker to its field code:

```json
{
  "fields":  {"[@Rosato2008]": "ADDIN ZOTERO_ITEM CSL_CITATION {…}"},
  "visible": {"[@Rosato2008]": "(1)"},
  "prefs":   [["ZOTERO_PREF_1", "…"], ["ZOTERO_PREF_2", "…"]]
}
```

```
python -m catena.cli inject draft.docx --plan plan.json --out draft-fields.docx
```

It never writes in place — the input is somebody's manuscript — and it guesses nothing: a marker with no entry in the plan is left exactly as it was typed and reported, and the command exits `1` when anything was skipped or not found. The rules that follow from what a real document is like:

- One marker maps to one field code, and the same marker may occur as many times as you like — each placement gets its own identifier, as Word requires. A paper cited twice with different **locators** (`p. 4`, `chapter 3`) therefore needs two distinct markers, because the locator lives in the field code and not in the marker.
- A marker must sit **inside one paragraph**, and a marker that **straddles a comment anchor is refused and reported** rather than risked: an unplaced marker is a nuisance, a broken comment is somebody's review lost.
- A field belonging to a tracked insertion stays inside it, so the sentence keeps its author. A marker inside tracked *deleted* text stays deleted and is not placed.
- **A suppressed-author field must cover only the parenthesis.** For a narrative citation — *Rosato et al. (2008) argue…* — the marker wraps `(2008)` and nothing else. If it swallows the name, Word's Refresh deletes the name from the sentence, because with the author suppressed Zotero renders only the year. This does not show before the Refresh: the defect lives in the boundary of the field, not in its content.

Pass `prefs` for a document that has never held Zotero fields; a document that is already a Zotero document has them, and existing custom properties are kept rather than overwritten.

## 8. What you do in Word, in order

catena leaves the **visible text of every citation empty on purpose**, and this is the step people get wrong. The Zotero API cannot pre-render a numeric style, because it renders each item alone and knows nothing of the order they appear in your document. Numbering is Word's job, and it takes one action:

1. Make sure **Zotero is running** on the same machine, and that you can see the library the citations point at — a group library resolves for anyone in the group; a URI tied to a local profile resolves for nobody else.
2. Open the injected copy — the `--out` file, not the original — in Word.
3. **Refresh** the document from the Zotero tab of the Word ribbon. This is what fills in the visible citation text, numbers a numeric style correctly, groups multiple items inside one field, and builds the bibliography where the bibliography field sits. Until you do it, each citation still displays the marker text you typed.
4. **Read the prose afterwards.** The Refresh is the moment the document changes, and it is the only moment a wrong field boundary becomes visible — above all for suppressed-author citations, where a name can vanish from a sentence that looked correct.
5. To change citation style, use Zotero's **Document Preferences** in Word and refresh again. That is why the `ZOTERO_PREF` properties are written: from then on the document is an ordinary Zotero document, and style is Word's business and not catena's. Verified end to end from Vancouver to APA on a document Zotero never wrote.

What Word does that catena cannot do for you: render citations in a style, number them in order of appearance, merge grouped items into one parenthesis, and assemble the bibliography. What catena cannot fix afterwards either: metadata that arrived wrong. **No tool here edits an item that already exists** — a swapped first name and surname, a chapter DOI that resolved to the editor — so those corrections happen in Zotero, and then a Refresh brings them into the document.

## 9. Driving catena from a chat (MCP)

catena is built to be used from a conversation with an AI assistant, which is where a reference list is usually discussed anyway. Create a key in **Profile → MCP keys**, one per client, and give your client the endpoint `https://catena.borant.eu/mcp` with the key in an `X-API-Key` header. Clients that cannot set headers can use `https://catena.borant.eu/mcp/k/<your-key>` instead — that URL *is* the credential and ends up in access logs, so use a separate key for it and revoke rather than share.

Sixteen tools. Ten are reads and free: what your key reaches, what a collection holds, what an identifier resolves to, and the field codes for a citation, a bibliography and the document properties. Six write, and your assistant should confirm each with you first: creating a collection or a binding, and the ingest tools of §5.

**What it reaches is exactly what you reach.** The key carries your identity, so it inherits the perimeter of your Zotero key and nothing more; a group you cannot see does not exist as far as your assistant is concerned. **What it cannot do:** touch your document — files are binary and a manuscript does not fit in a conversation, which is why the injector runs on your machine — delete anything from a library, or edit an item that already exists. The worst this surface can do is add a collection nobody wanted.

## 10. What catena does not do

- **It does not write prose.** A tool that can put its hands in both the bibliography and the text is a tool you have to trust twice; this one asks you to trust it once, about something you can check.
- **Footnote and note-based styles are not supported**, and are refused rather than half-written. So are documents that mix Zotero fields with hand-typed citations: both are waiting for a real exemplar to be calibrated against.
- It does not decide between two papers with the same title, and it does not invent an item when an identifier will not resolve — it says so and stops.
- It does not delete anything, from a library or from a document, and it does not edit an existing Zotero item.
- It does not manage your bibliography, track your submissions, or search the literature for you. It is the connective tissue between the tools that do.

## 11. Data protection

catena holds a person in four places, and one of them deserves a straight answer:

- **`users`** — your name, your email address, and your Borant ID subject.
- **`zotero_credentials`** — your Zotero API key, **in clear text**, plus the permissions snapshot taken when it was saved. The snapshot is Zotero's own answer about the key, kept so the app can still explain months later why a key was accepted; it carries a second copy of the key itself. The interface never shows either copy and never sends them back to the browser, but a database dump contains both.
- **`bindings` and `ingest_events`** — the labels you chose and the identifiers you added, which say what you are working on.

Holding a Zotero API key means catena, and anyone with access to its database, can read every library that key reaches and write to the deposit group. That is the reason the perimeter check in §2 refuses a wide key before it is ever saved — a key that can write neither to your personal library nor to arbitrary groups does limited damage if it escapes. It is not a reason to leave one lying around: revoke it on zotero.org when a project ends, and remember that an MCP key is a second door onto the same reach.

Two things that do not happen. **Your manuscript never reaches the server**: the audit, the discovery and the injection all run on your own machine, and the server only ever sees identifiers and item keys. And **the items you add are not ours**: they live in your own Zotero account, and they are deleted from Zotero.

What does leave the machine: identifiers you resolve go to Zotero's API and, through the translation server, to the source that owns them — resolving a URL means that page is fetched. Removing somebody from Borant ID does not remove them from here; a deletion request is served by deleting these rows.

## 12. Good practices

- **One paper, one collection, one binding**, with a deposit group you own. Two papers sharing a collection is the state the whole design exists to avoid.
- **Give the key the narrow perimeter and keep it narrow.** If catena refuses your key, fix it on zotero.org rather than looking for the setting that makes the refusal go away — the refusal is the feature.
- **Read the plan before applying it.** Rows marked `ambiguous` or carrying an author-mismatch warning are the ones that turn into a wrong reference that reads perfectly.
- **Paste identifiers from the source, never from memory** — yours or a model's. A mistyped DOI resolves to somebody else's paper and looks entirely correct.
- **Audit a draft you inherit** before touching it, and audit it again after the Refresh. The duplicate surrogate is invisible in Word until it doubles a bibliography entry.
- **Keep the original.** The injector writes to a copy by design; keep that habit when you rename files afterwards.
- **Fix metadata in Zotero, not in the document.** A field carries a snapshot of the item, and the Refresh is what brings a correction across.
- **Do the Refresh yourself, and then read the sentences.** catena can place a field in the right position; only a person can see that the sentence still says what it meant.
