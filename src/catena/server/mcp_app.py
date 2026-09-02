"""
The model-facing surface of catena.

This is where the tool earns its name. A conversation can ask what a paper's
collection holds, find an item, and get back a Word field ready to be placed —
without anyone retyping a DOI, a title, or an item key, which is the step where
bibliographies go wrong.

Access. Every call runs as the person who owns the API key, and every Zotero
request goes out with *their* key. catena holds no credential of its own, so a
caller reaches exactly what its owner reaches and there is no configuration that
could widen that. An account with no Zotero key reaches nothing at all.

Reads are free. Writes are confined to the binding's deposit library, and there
is no tool here that deletes anything or edits an item that already exists —
the worst this surface can do is add a collection nobody wanted.

Errors come back as {"error": ...} rather than raised: a tool that throws hands
the model a stack trace to hallucinate around, while a sentence it can read lets
it correct course.
"""

from __future__ import annotations

import functools
import inspect

from mcp.server.mcpserver import MCPServer

from .. import build
from . import auth
from .models import Binding, SessionLocal, ZoteroCredential
from .zotero_client import ZoteroError, zot_for

mcp = MCPServer(
    name="catena",
    instructions=(
        "Zotero references inside Word documents. A binding ties one paper to "
        "one collection: it reads from the library where that paper's "
        "references live and deposits new items into a library your key can "
        "write to, which are usually two different places. Start with "
        "list_bindings, or list_libraries if there are none yet. Reads are "
        "free; confirm with the user before any write. citation_field returns "
        "a field code to place in a .docx — the visible text is filled in by a "
        "single Refresh in Word, which is also what numbers a numeric style "
        "correctly."
    ),
)


def _fail(msg: str) -> dict:
    return {"error": msg}


class _Ctx:
    """Everything a tool needs: the session, the caller, and their Zotero."""

    def __init__(self):
        self.db = SessionLocal()
        self.user = auth.current_caller()

    def zot(self):
        cred = (
            self.db.query(ZoteroCredential)
            .filter(ZoteroCredential.user_id == self.user.id)
            .first()
        )
        return zot_for(cred)

    def binding(self, label: str) -> Binding:
        row = (
            self.db.query(Binding)
            .filter(Binding.user_id == self.user.id, Binding.label == label)
            .first()
        )
        if not row:
            known = [
                b.label
                for b in self.db.query(Binding).filter(Binding.user_id == self.user.id)
            ]
            raise LookupError(
                f"no binding called {label!r}. "
                + (f"You have: {', '.join(known)}." if known else "You have none yet.")
            )
        return row

    def close(self):
        self.db.close()


def _tool(fn):
    """Open a context, run, turn the expected failures into readable messages.

    The signature matters as much as the body: MCP builds a tool's schema by
    inspecting it, so the wrapper has to advertise the real parameters minus
    `ctx`. A bare *args/**kwargs wrapper compiles, registers, and then offers
    the model two arguments called `a` and `kw` — which is exactly how this was
    wrong the first time.
    """
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())[1:]  # drop ctx

    @functools.wraps(fn)
    def wrapped(*a, **kw):
        try:
            ctx = _Ctx()
        except PermissionError as e:
            return _fail(str(e))
        try:
            return fn(ctx, *a, **kw)
        except (LookupError, ValueError, ZoteroError) as e:
            return _fail(str(e))
        finally:
            ctx.close()

    wrapped.__signature__ = sig.replace(parameters=params)
    wrapped.__annotations__ = {
        k: v for k, v in getattr(fn, "__annotations__", {}).items() if k != "ctx"
    }
    return wrapped


def _binding_brief(b: Binding) -> dict:
    return {
        "label": b.label,
        "reads_from": b.source_library,
        "source_collection": b.source_collection_key,
        "deposits_into": b.deposit_library,
        "deposit_collection": b.deposit_collection_key,
        "style": b.style_name,
        "locale": b.locale,
        "single_leg": b.one_legged,
        "papertrail_project": b.papertrail_project_id,
    }


# --- reads -------------------------------------------------------------------


@mcp.tool()
@_tool
def list_libraries(ctx) -> dict:
    """Zotero libraries this key reaches, with whether it can write to them.

    Worth reading before creating a binding: the deposit library has to be one
    of the writable ones, and there is usually exactly one.
    """
    libs = ctx.zot().libraries()
    return {
        "you": ctx.user.name,
        "libraries": [
            {"library": l.library, "name": l.name, "writable": l.writable} for l in libs
        ],
    }


@mcp.tool()
@_tool
def list_collections(ctx, library: str) -> dict:
    """Collections in one library. `library` is users/<id> or groups/<id>."""
    return {"library": library, "collections": ctx.zot().collections(library)}


@mcp.tool()
@_tool
def list_bindings(ctx) -> dict:
    """Your bindings: one paper, one collection."""
    rows = (
        ctx.db.query(Binding)
        .filter(Binding.user_id == ctx.user.id)
        .order_by(Binding.updated_at.desc())
        .all()
    )
    return {"bindings": [_binding_brief(b) for b in rows]}


@mcp.tool()
@_tool
def get_binding(ctx, label: str) -> dict:
    """One binding in full."""
    return _binding_brief(ctx.binding(label))


@mcp.tool()
@_tool
def collection_items(ctx, label: str, limit: int = 100) -> dict:
    """What a binding's collections hold, both legs merged.

    Items are returned with the key and library needed to cite them. Anything
    that appears on both legs is flagged: the same paper reached through two
    different URIs produces two bibliography entries and two numbers — in APA a
    year disambiguation like 2008a/2008b that does not exist. When that
    happens, cite the source-leg copy.
    """
    b = ctx.binding(label)
    z = ctx.zot()

    source = (
        z.collection_items(b.source_library, b.source_collection_key, limit)
        if b.source_collection_key
        else []
    )
    deposit = (
        z.collection_items(b.deposit_library, b.deposit_collection_key, limit)
        if b.deposit_collection_key and not b.one_legged
        else []
    )

    def sig(it: dict) -> str:
        if it.get("doi"):
            return "doi:" + it["doi"].lower()
        return f"t:{(it.get('title') or '').lower()[:80]}|{it.get('year') or ''}"

    seen = {sig(i): i for i in source}
    duplicates = []
    for i in deposit:
        twin = seen.get(sig(i))
        if twin:
            duplicates.append(
                {
                    "title": i["title"][:70],
                    "source": f"{twin['library']}/{twin['key']}",
                    "deposit": f"{i['library']}/{i['key']}",
                    "cite": f"{twin['library']}/{twin['key']}",
                }
            )

    def strip(it: dict) -> dict:
        return {k: v for k, v in it.items() if k != "csljson"}

    return {
        "binding": b.label,
        "source": [strip(i) for i in source],
        "deposit": [strip(i) for i in deposit],
        "duplicates_across_legs": duplicates,
        "note": (
            "Cite the source-leg copy of anything listed under "
            "duplicates_across_legs, or the bibliography doubles."
            if duplicates
            else ""
        ),
    }


@mcp.tool()
@_tool
def search_library(ctx, query: str, library: str = "", limit: int = 25) -> dict:
    """Lexical search in one library, or across every readable one.

    Lexical, not semantic: no hit means those words are not in the title,
    creators or full text Zotero indexed — never that the paper is absent.
    """
    z = ctx.zot()
    targets = [library] if library else [l.library for l in z.libraries()]
    out = []
    for lib in targets:
        try:
            for it in z.search(lib, query, limit):
                out.append({k: v for k, v in it.items() if k != "csljson"})
        except ZoteroError:
            continue
        if len(out) >= limit:
            break
    return {"query": query, "searched": targets, "results": out[:limit]}


@mcp.tool()
@_tool
def citation_field(
    ctx,
    label: str,
    keys: list[str],
    locator: str = "",
    locator_label: str = "",
    prefix: str = "",
    suffix: str = "",
    suppress_author: bool = False,
) -> dict:
    """A Word field code for one citation, ready to place in a .docx.

    More than one key makes a *grouped* citation — one field, several items,
    which renders as (1,2) rather than as two adjacent fields. That is the
    normal case more often than not: in a real draft, 45% of citation
    parentheses grouped two or more references.

    Keys are looked up on the source leg first and then on the deposit leg, so
    a paper that exists in both is cited from the copy co-authors can resolve.

    The visible text comes back empty on purpose. One Refresh in Word fills it
    in, numbering and grouping included; the Zotero API cannot do it for a
    numeric style, because it renders each item alone and knows nothing about
    the order they appear in the document.
    """
    b = ctx.binding(label)
    z = ctx.zot()
    if not keys:
        return _fail("citation_field needs at least one item key")

    legs = [b.source_library] + ([] if b.one_legged else [b.deposit_library])
    entries, resolved = [], []
    for key in keys:
        item = None
        for lib in legs:
            got = z.items(lib, [key])
            if got:
                item = got[0]
                break
        if not item:
            return _fail(
                f"item {key} is in neither leg of {label} "
                f"({' or '.join(legs)}). Check the key, or add it first."
            )
        entries.append(
            build.citation_item(
                item["library"],
                item["key"],
                item["csljson"],
                locator=locator or None,
                label=locator_label or None,
                prefix=prefix or None,
                suffix=suffix or None,
                suppress_author=suppress_author,
            )
        )
        resolved.append(
            {
                "key": item["key"],
                "library": item["library"],
                "cited": f"{item['first_author'] or '?'} {item['year'] or '?'}",
            }
        )

    return {
        "field_code": build.citation_field(entries),
        "items": resolved,
        "grouped": len(entries) > 1,
        "note": "Place as a Word field; the visible text appears after a Refresh.",
    }


@mcp.tool()
@_tool
def bibliography_field(ctx) -> dict:
    """The field code that marks where the bibliography goes."""
    return {
        "field_code": build.BIBLIOGRAPHY_FIELD,
        "note": "One per document, where the reference list should appear.",
    }


@mcp.tool()
@_tool
def document_prefs(ctx, label: str) -> dict:
    """The ZOTERO_PREF custom properties a document needs to be Zotero's.

    These are not a field: they belong in docProps/custom.xml. Once they are
    there, changing citation style is Document Preferences in Word, not
    catena's job.
    """
    b = ctx.binding(label)
    prefs = build.document_prefs(b.csl_style, b.locale)
    return {
        "style": b.style_name,
        "locale": b.locale,
        "properties": [{"name": n, "value": v} for n, v in prefs],
        "custom_xml": build.custom_properties_xml(prefs),
    }


# --- writes: always confirm with the user first ------------------------------


@mcp.tool()
@_tool
def create_binding(
    ctx,
    label: str,
    source_library: str,
    deposit_collection: str,
    source_collection: str = "",
    deposit_library: str = "",
    style: str = "http://www.zotero.org/styles/vancouver",
    locale: str = "en-GB",
    papertrail_project: str = "",
) -> dict:
    """Tie a paper to its collections. Confirm with the user before calling.

    `source_library` is where the paper's references already live, usually
    read-only. `deposit_library` defaults to the one writable library the key
    has; `deposit_collection` is created if it does not exist there.
    """
    label = label.strip()
    if not label:
        return _fail("a binding needs a label")
    if (
        ctx.db.query(Binding)
        .filter(Binding.user_id == ctx.user.id, Binding.label == label)
        .first()
    ):
        return _fail(f"you already have a binding called {label!r}")

    z = ctx.zot()
    libs = z.libraries()
    writable = [l.library for l in libs if l.writable]
    readable = [l.library for l in libs]

    if source_library not in readable:
        return _fail(
            f"{source_library} is not a library your key reaches. "
            f"You have: {', '.join(readable)}"
        )
    deposit = deposit_library or (writable[0] if len(writable) == 1 else "")
    if not deposit:
        return _fail(
            "no deposit library: your key can write to "
            + (f"{len(writable)} libraries, so name one" if writable else "none")
        )
    if deposit not in writable:
        return _fail(f"{deposit} is not writable by your key")

    src_key = None
    if source_collection:
        found = z.find_collection(source_library, source_collection)
        if not found:
            return _fail(
                f"no collection called {source_collection!r} in {source_library}"
            )
        src_key = found["key"]

    dep = z.find_collection(deposit, deposit_collection)
    if not dep:
        return _fail(
            f"no collection called {deposit_collection!r} in {deposit}. "
            "Create it with create_collection first, so that making a "
            "collection is always a separate, visible act."
        )

    row = Binding(
        user_id=ctx.user.id,
        label=label,
        source_library=source_library,
        source_collection_key=src_key,
        deposit_library=deposit,
        deposit_collection_key=dep["key"],
        csl_style=style,
        locale=locale,
        papertrail_project_id=papertrail_project or None,
    )
    ctx.db.add(row)
    ctx.db.commit()
    return {"created": _binding_brief(row)}


@mcp.tool()
@_tool
def create_collection(ctx, name: str, library: str = "") -> dict:
    """Create a collection in a writable library. Confirm with the user first."""
    z = ctx.zot()
    writable = [l.library for l in z.libraries() if l.writable]
    target = library or (writable[0] if len(writable) == 1 else "")
    if not target:
        return _fail("name the library: your key can write to " + str(writable))
    if target not in writable:
        return _fail(f"{target} is not writable by your key")
    if z.find_collection(target, name):
        return _fail(f"{target} already has a collection called {name!r}")

    from .zotero_client import parse_library
    from pyzotero import zotero as _z

    ident, kind = parse_library(target)
    client = _z.Zotero(ident, kind, z.api_key)
    resp = client.create_collections([{"name": name}])
    created = (resp.get("successful") or {}).get("0") or {}
    return {
        "created": {
            "library": target,
            "name": name,
            "key": created.get("key") or (created.get("data") or {}).get("key"),
        }
    }
