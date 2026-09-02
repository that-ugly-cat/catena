"""
Turning an identifier into real metadata, via Zotero's own translation server.

The rule this module exists to keep is the one in SPEC §5: **catena never
invents an item**. Metadata comes from the same translators that sit behind the
Zotero connector button, or it does not come at all. A model filling in a title
and a journal from memory produces an item that looks exactly like a real one,
which is the only error in this system that nobody can see.

Two endpoints, both taking plain text:

    POST /search   DOI, ISBN, PMID, arXiv id   -> Zotero API JSON
    POST /web      a URL                       -> Zotero API JSON, or 300 with
                                                  a choice of several

The ladder — DOI, PMID/arXiv, ISBN, URL, then stop — matters because of what the
library actually holds: measured on a real one, DOIs cover 93% of journal
articles and almost nothing else, while roughly one citable item in five has no
DOI at all. The URL rung is what makes grey literature reachable.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_URL = "http://translation:1969"
TIMEOUT = 30

RE_DOI = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)$", re.I)
RE_PMID = re.compile(r"^(?:pmid:\s*)?(\d{1,8})$", re.I)
RE_ARXIV = re.compile(r"^(?:arxiv:\s*)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7})$", re.I)
RE_ISBN = re.compile(r"^(?:isbn:\s*)?((?:97[89])?[\d-]{9,16}[\dXx])$", re.I)
RE_URL = re.compile(r"^https?://", re.I)


class TranslationError(RuntimeError):
    pass


@dataclass
class Resolution:
    identifier: str
    kind: str  # doi | pmid | arxiv | isbn | url
    items: list[dict]

    @property
    def item(self) -> dict | None:
        return self.items[0] if self.items else None

    @property
    def item_type(self) -> str | None:
        return (self.item or {}).get("itemType")


def classify(identifier: str) -> str | None:
    """Which rung of the ladder this identifier is on, or None for no rung.

    Order matters: a DOI can be written as a doi.org URL, and it is a DOI first.
    """
    s = (identifier or "").strip()
    if not s:
        return None
    if RE_DOI.match(s):
        return "doi"
    if RE_ARXIV.match(s):
        return "arxiv"
    if RE_ISBN.match(s.replace(" ", "")):
        return "isbn"
    if RE_URL.match(s):
        return "url"
    if RE_PMID.match(s):
        return "pmid"
    return None


def normalise(identifier: str, kind: str) -> str:
    s = identifier.strip()
    if kind == "doi":
        return RE_DOI.match(s).group(1)
    if kind == "pmid":
        return RE_PMID.match(s).group(1)
    if kind == "arxiv":
        return RE_ARXIV.match(s).group(1)
    if kind == "isbn":
        return RE_ISBN.match(s.replace(" ", "")).group(1).replace("-", "")
    return s


class Translation:
    def __init__(self, base_url: str = DEFAULT_URL):
        self.base_url = base_url.rstrip("/")

    def _post(self, path: str, body: str, content_type: str) -> tuple[int, str]:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body.encode("utf-8"),
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as e:
            raise TranslationError(
                f"cannot reach the translation server at {self.base_url}: {e.reason}. "
                "It runs as a container beside catena; see DEPLOY.md."
            ) from e

    def resolve(self, identifier: str) -> Resolution:
        """Metadata for one identifier, or a message saying why not."""
        kind = classify(identifier)
        if not kind:
            raise TranslationError(
                f"{identifier!r} is not a DOI, PMID, arXiv id, ISBN or URL. "
                "catena resolves those and nothing else: it does not guess an "
                "item from a title, because a guessed item is indistinguishable "
                "from a real one."
            )
        value = normalise(identifier, kind)
        path, body, ctype = (
            ("/web", value, "text/plain")
            if kind == "url"
            else ("/search", value, "text/plain")
        )
        status, text = self._post(path, body, ctype)

        if status == 300:
            # /web found several candidates and wants one chosen. Choosing for
            # the user is exactly the judgement this module refuses to make.
            try:
                choices = json.loads(text)
            except json.JSONDecodeError:
                choices = {}
            raise TranslationError(
                f"{value} offers {len(choices) or 'several'} possible items; "
                "the page holds more than one thing. Give a direct link to the "
                "item itself, or add it with the Zotero connector."
            )
        if status == 501 or (status == 400 and "No identifiers" in text):
            raise TranslationError(
                f"nothing resolved {value}. It may be wrong, or unregistered, or "
                "the page may not be one any translator understands."
            )
        if status != 200:
            raise TranslationError(f"translation server answered {status}: {text[:200]}")

        try:
            items = json.loads(text)
        except json.JSONDecodeError as e:
            raise TranslationError("the translation server sent something unreadable") from e
        if isinstance(items, dict):
            items = [items]
        items = [i for i in items if i.get("itemType") not in ("attachment", "note")]
        if not items:
            raise TranslationError(f"{value} resolved to nothing citable")
        return Resolution(identifier=value, kind=kind, items=items)

    def alive(self) -> bool:
        try:
            self._post("/search", "10.1016/S0140-6736(08)61406-3", "text/plain")
            return True
        except TranslationError:
            return False


def first_author(item: dict) -> str | None:
    for c in item.get("creators") or []:
        name = c.get("lastName") or c.get("name")
        if name:
            return name.strip()
    return None


def brief(item: dict) -> dict:
    """What a person needs to see before agreeing to add this."""
    return {
        "type": item.get("itemType"),
        "title": (item.get("title") or "")[:140],
        "first_author": first_author(item),
        "year": (item.get("date") or "")[:4] or None,
        "container": item.get("publicationTitle") or item.get("publisher") or None,
        "doi": item.get("DOI") or None,
        "isbn": item.get("ISBN") or None,
        "url": item.get("url") or None,
    }
