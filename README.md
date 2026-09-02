# catena

Zotero references inside Word documents, with the reason for the citation attached.

The name comes from the medieval *catena*: a compilation of passages quoted from different authorities, chained to the text they comment on. It also holds literally, for the chain of identifiers that runs from a literature search to the field inside the `.docx` without anyone retyping anything.

`catena` is **not** a bibliography manager (that is Zotero), not a project tracker (PaperTrail), not a literature search engine (Contrarian). It is the connective tissue between the three. And it does not write prose: it inserts, updates and removes references, and nothing else.

The full specification is in [SPEC.md](SPEC.md), closed at version 1.0 and written in Italian. Every technical claim in it was verified against real material, and it says where.

## Status

First piece: the **audit**, read-only. No network, no credentials, no dependencies — everything it checks can be worked out from the file.

```bash
python -m catena.cli audit manuscript.docx
```

It reports:

- **duplicate surrogates** — the same paper cited under two different URIs, which produces two bibliography entries and two numbers. In APA it becomes a year disambiguation (`2008a`/`2008b`) that does not exist. This is the nastiest defect of the lot, because the document looks correct;
- **URIs tied to a local Zotero profile**, which resolve on exactly one machine in the world and are orphans to every co-author;
- **items with no URI or no embedded data** — citations that will break in somebody else's hands;
- **dirty metadata** — DOIs that are not DOIs, missing titles;
- duplicate `citationID`s, unexpected `fieldType`, missing bibliography, footnote styles (not handled yet), tracked changes that must be preserved.

Exit code: `0` clean, `1` at least one error (or a warning with `--strict`), `2` unreadable file.

## Why the audit first

It is already worth something on manuscripts that exist, written by other people and years ago; it does not require the rest of `catena` to be ready; and it is how `catena` will check itself once it is. On the spike fixture it finds, **statically**, the defect that could previously only be seen by opening Word.

## The server

A minimal web app plus the MCP surface, on the same scaffolding as the other borant tools: FastAPI, JWT in an httpOnly cookie, SQLite in a single file, Docker behind Caddy. See [DEPLOY.md](DEPLOY.md).

Three pages — sign in, bindings, profile — and the profile is the one that matters. It does two things.

**It configures the Zotero key, and refuses it if the perimeter is wrong.** `catena` has no credentials of its own towards Zotero: it uses the user's, and reaches exactly as far as they do. But a key is not acceptable merely because it works. The case the validator exists to catch is subtle and happens in real life: `access.groups` may carry an `all` entry acting as the default for groups not listed, and with `all.write = true` the `write: false` entries on today's groups look like a narrow perimeter while **every future group is born writable**. The perimeter is not fixed: it grows on its own. The accepted shape is the one where the default denies and the exception is a single explicit one:

```
Personal Library  : library access, NO write, NO files
All Groups        : Read Only
<deposit group>   : Read/Write   ← the only exception
```

**It manages the MCP keys.** One key per client, bound to a person: it carries their identity and therefore the reach of their Zotero key, no more and no less. Header `X-API-Key`, or the path variant for clients that cannot send custom headers — with the caveat that such a URL *is* the credential and ends up in access logs.

## Signing in: local, or Borant ID

Two modes, and `local` is the default on purpose — an app that believes an identity header with nothing in front of it lets in anyone who can send that header.

```
AUTH_MODE=local     (default)   email and password against this app's own table
AUTH_MODE=gateway               Borant ID vouches for the caller via X-Borant-*
```

In gateway mode `catena` never talks to Borant ID: it reads the headers Caddy attaches, and only when the request comes from `BORANT_TRUSTED_PROXY`. Lookup is by `borant_sub` and never by email, because an email is something a gate operator types and a subject is not: matching on it would let a typo hand one person another person's Zotero key. An unknown subject gets a fresh profile, which is harmless here in a way worth stating — a new profile has no Zotero credential, so it reaches no library at all, and the failure mode is an empty page rather than a leak.

Existing local accounts are joined to their subject with `link_borant.py`, by hand, once. It has to be by hand for the same reason.

The MCP surface is unaffected by either mode: a model client has no browser and no cookie, so its own key stays the only credential it can carry.

## Development

```bash
cp .env.example .env
printf 'JWT_SECRET=%s\n' "$(openssl rand -hex 32)" >> .env
uv run --env-file .env --extra server python seed.py you@example.org "You" pw
uv run --env-file .env --extra server uvicorn catena.server.main:app --reload --port 8022
uv run --extra server --with pytest python -m pytest tests -q
```

`spike/` holds the fixture generator: a `.docx` with Zotero fields built by hand in OOXML, opened in Word, refreshed and switched from Vancouver to APA. It is the proving ground for everything else — see [spike/README.md](spike/README.md).

## What is missing

The MCP server, ingest through the translation server, the injector. The intended flows are described in SPEC §13. Two cases still have no real exemplar and the code refuses them explicitly rather than guessing: mixed Zotero + hand-typed documents (§13.5) and footnote styles (§7.6).
