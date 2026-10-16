"""
Talking to Zotero on a person's behalf.

Every call here runs with a user's own key, so catena reaches exactly what that
user reaches and no further. There is no system credential and no way to add
one: the reach of the MCP surface is a property of the caller, not of the
service.

The library address is carried around as `users/<id>` or `groups/<id>` — the
same form that goes into a citation URI (SPEC §7.2) — so that one string
identifies a library everywhere, from the binding row to the field code.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from pyzotero import zotero

from .models import ZoteroCredential
from .records import field_of, year_of
from .zotero_key import ZoteroError, fetch_current_key

RE_LIBRARY = re.compile(r"^(users|groups)/(\d+)$")

# The API accepts up to 100 per page. Read paths that could run long ask for
# everything and let pyzotero page; the caps here are on what we hand back to a
# model, which is a different concern from what the API will give.
PAGE = 100


def parse_library(library: str) -> tuple[str, str]:
    """`groups/6378365` -> ("6378365", "group"). Raises on anything else."""
    m = RE_LIBRARY.match((library or "").strip("/"))
    if not m:
        raise ValueError(
            f"library must be users/<id> or groups/<id>, got {library!r}"
        )
    kind, ident = m.groups()
    return ident, "user" if kind == "users" else "group"


def _is_not_found(exc: Exception) -> bool:
    """Whether a pyzotero failure means "no such item" rather than "broken"."""
    text = str(exc).lower()
    return "code: 404" in text or "does not exist" in text


@dataclass
class LibraryInfo:
    library: str
    name: str
    writable: bool


class Zot:
    """A user's Zotero access, with the perimeter their key defines."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._scope_cache: dict | None = None
        self._names: dict[str, str] = {}

    # -- what the key can see ------------------------------------------------

    def _scope(self) -> dict:
        if self._scope_cache is None:
            self._scope_cache = fetch_current_key(self.api_key)
        return self._scope_cache

    def libraries(self) -> list[LibraryInfo]:
        """Every library this key reaches, personal first, with write flags.

        Group names cost one extra request each and are worth it: `groups/6378365`
        means nothing to a human, and this list is read by a model that has to
        choose one.
        """
        payload = self._scope()
        access = payload.get("access") or {}
        out: list[LibraryInfo] = []

        user = access.get("user") or {}
        if user.get("library"):
            uid = str(payload.get("userID"))
            out.append(
                LibraryInfo(
                    library=f"users/{uid}",
                    name=payload.get("username") or "personal library",
                    writable=bool(user.get("write")),
                )
            )

        groups = access.get("groups") or {}
        default_write = bool((groups.get("all") or {}).get("write"))
        gids = [g for g in sorted(groups) if g != "all"]
        names = self._group_names(gids)
        for gid in gids:
            rule = groups[gid]
            out.append(
                LibraryInfo(
                    library=f"groups/{gid}",
                    name=names.get(gid, f"group {gid}"),
                    writable=bool(rule.get("write", default_write)),
                )
            )
        return out

    def _group_names(self, gids: list[str]) -> dict[str, str]:
        """Group titles, cached per instance and tolerant of failures.

        A name costs one request. It is worth it because `groups/6378365` means
        nothing to a human, and this list is read by a model choosing one.
        """
        missing = [g for g in gids if g not in self._names]
        for gid in missing:
            req = urllib.request.Request(
                f"https://api.zotero.org/groups/{gid}",
                headers={"Zotero-API-Version": "3", "Zotero-API-Key": self.api_key},
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    payload = json.loads(r.read().decode())
                self._names[gid] = (payload.get("data") or {}).get("name") or f"group {gid}"
            except (urllib.error.URLError, json.JSONDecodeError, ValueError):
                self._names[gid] = f"group {gid}"
        return self._names

    # -- reads ---------------------------------------------------------------

    def _client(self, library: str) -> zotero.Zotero:
        ident, kind = parse_library(library)
        return zotero.Zotero(ident, kind, self.api_key)

    def collections(self, library: str) -> list[dict]:
        z = self._client(library)
        rows = z.everything(z.collections())
        return [
            {
                "key": c["key"],
                "name": c["data"]["name"],
                "parent": c["data"].get("parentCollection") or None,
                "items": c["meta"].get("numItems", 0),
            }
            for c in rows
        ]

    def find_collection(self, library: str, name: str) -> dict | None:
        needle = name.strip().lower()
        for c in self.collections(library):
            if c["name"].strip().lower() == needle:
                return c
        return None

    @staticmethod
    def _paged(z: zotero.Zotero, rows: list[dict], limit: int) -> list[dict]:
        """Keep following the 'next' link until `limit` rows are in hand.

        Zotero's API answers one page at a time, and `PAGE` is its ceiling per
        request. Without this, asking for more than a page silently returned a
        page: `ingest` builds the duplicate index of a plan from
        `collection_items(..., 500)`, so on a collection larger than one page a
        paper already sitting in it came back as `new` and was added a second
        time. The unique row on (binding, identifier) does not catch that — it
        catches the same identifier twice, not the same paper reached by a
        different one.
        """
        while len(rows) < limit:
            more = z.follow()
            if not more:
                break
            rows = rows + more
        return rows[:limit]

    def collection_items(self, library: str, collection_key: str, limit: int = 200) -> list[dict]:
        """Top-level items of a collection, with CSL-JSON attached.

        `include=csljson,data` is what makes this useful: the CSL is what goes
        into a citation field verbatim, and asking Zotero for it avoids
        rebuilding the mapping from Zotero's own item schema — which is the
        fiddly, silently-wrong-forever kind of code.
        """
        z = self._client(library)
        z.add_parameters(include="csljson,data", limit=min(limit, PAGE))
        rows = self._paged(z, z.collection_items_top(collection_key), limit)
        return [self._shape(library, r) for r in rows]

    def search(self, library: str, query: str, limit: int = 25) -> list[dict]:
        z = self._client(library)
        z.add_parameters(q=query, include="csljson,data", limit=min(limit, PAGE))
        rows = self._paged(z, z.items(), limit)
        return [self._shape(library, r) for r in rows]

    def items(self, library: str, keys: list[str]) -> list[dict]:
        """Specific items by key, in the order asked for; missing ones absent.

        A key that is not in this library is **not** an error here. Callers walk
        the legs of a binding in turn, so "not here" is an ordinary answer and
        has to look like one — otherwise the first leg that misses aborts the
        search and the model gets pyzotero's raw dump, URL and all, instead of a
        sentence saying which key was not found where. Anything that is not a
        404 still raises: that one is a real failure and hiding it would turn a
        broken key or a network fault into a silently short list.
        """
        z = self._client(library)
        found: dict[str, dict] = {}
        for key in keys:
            z.add_parameters(include="csljson,data")
            try:
                row = z.item(key)
            except Exception as e:  # pyzotero has its own exception hierarchy
                if _is_not_found(e):
                    continue
                raise ZoteroError(f"{library}/{key}: {e}") from e
            found[key] = self._shape(library, row)
        return [found[k] for k in keys if k in found]

    # -- item types ------------------------------------------------------------

    # Zotero's schema is global and changes with a client release, not per key:
    # one fetch per process is enough, and it keeps a plan of forty caller
    # records from costing eighty requests.
    _fields: dict[str, list[str]] = {}
    _creators: dict[str, list[str]] = {}

    def fields(self, item_type: str) -> list[str]:
        """The fields an item type accepts, as Zotero names them."""
        if item_type not in Zot._fields:
            z = zotero.Zotero("0", "user", self.api_key)
            Zot._fields[item_type] = [f["field"] for f in z.item_type_fields(item_type)]
        return Zot._fields[item_type]

    def creator_types(self, item_type: str) -> list[str]:
        """The creator types an item type accepts, primary first."""
        if item_type not in Zot._creators:
            z = zotero.Zotero("0", "user", self.api_key)
            Zot._creators[item_type] = [
                c["creatorType"] for c in z.item_creator_types(item_type)
            ]
        return Zot._creators[item_type]

    @staticmethod
    def _shape(library: str, row: dict) -> dict:
        data = row.get("data") or {}
        csl = row.get("csljson") or {}
        creators = data.get("creators") or []
        first = next(
            (c.get("lastName") or c.get("name") for c in creators if c.get("lastName") or c.get("name")),
            None,
        )
        return {
            "key": row.get("key"),
            "library": library,
            "type": data.get("itemType"),
            "title": field_of(data, "title") or "",
            "first_author": first,
            "year": year_of(field_of(data, "date")),
            "doi": data.get("DOI") or None,
            "isbn": data.get("ISBN") or None,
            "url": data.get("url") or None,
            "csljson": csl,
        }


def zot_for(cred: ZoteroCredential | None) -> Zot:
    if not cred or not cred.key:
        raise ZoteroError(
            "no Zotero key configured for this account — set one on /profile. "
            "catena has no credentials of its own, so without yours it sees no "
            "library at all."
        )
    return Zot(cred.key)
