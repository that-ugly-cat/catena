"""
Zotero key validation — SPEC §2.2.

A Zotero key is not acceptable merely because it works: it has to have the right
perimeter. The case this exists to catch is subtle, and it happens in real life —
it happened while the specification was being written, on 2 September 2026.

`access.groups` may carry an `all` entry that acts as the default for groups not
listed explicitly. With `all.write = true`, the `write: false` entries on the
existing groups look like a narrow perimeter, but **every group created or
joined from then on is born writable**. The perimeter is not fixed: it grows on
its own, and six months later nobody remembers.

The verdict:

    ok      the default denies, the exception is explicit and limited
    wide    write on the personal library, or groups.all.write -> REFUSED
    narrow  no writable group at all: catena has nowhere to deposit
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API = "https://api.zotero.org"
TIMEOUT = 15

OK, WIDE, NARROW = "ok", "wide", "narrow"


@dataclass
class KeyScope:
    verdict: str
    reasons: list[str] = field(default_factory=list)
    user_id: str | None = None
    username: str | None = None
    personal_write: bool = False
    groups_all_write: bool = False
    writable_groups: list[str] = field(default_factory=list)
    readable_groups: list[str] = field(default_factory=list)
    raw: dict | None = None

    @property
    def usable(self) -> bool:
        return self.verdict == OK

    @property
    def raw_json(self) -> str:
        return json.dumps(self.raw, indent=2, sort_keys=True) if self.raw else ""


class ZoteroError(RuntimeError):
    pass


def fetch_current_key(api_key: str) -> dict:
    req = urllib.request.Request(
        f"{API}/keys/current",
        headers={"Zotero-API-Version": "3", "Zotero-API-Key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            raise ZoteroError(
                "Zotero does not recognise this key (403). Check that you copied "
                "it whole from zotero.org/settings/keys."
            ) from e
        raise ZoteroError(f"Zotero answered {e.code}.") from e
    except urllib.error.URLError as e:
        raise ZoteroError(f"Cannot reach api.zotero.org: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise ZoteroError("Zotero's answer could not be read.") from e


def evaluate(payload: dict) -> KeyScope:
    """The verdict on the perimeter, with the reasons spelled out."""
    access = payload.get("access") or {}
    user = access.get("user") or {}
    groups = access.get("groups") or {}
    all_rule = groups.get("all") or {}

    scope = KeyScope(
        verdict=OK,
        user_id=str(payload.get("userID")) if payload.get("userID") else None,
        username=payload.get("username"),
        personal_write=bool(user.get("write")),
        groups_all_write=bool(all_rule.get("write")),
        writable_groups=sorted(
            g for g, v in groups.items() if g != "all" and v.get("write")
        ),
        readable_groups=sorted(
            g for g, v in groups.items() if g != "all" and not v.get("write")
        ),
        raw=payload,
    )

    if scope.personal_write:
        scope.reasons.append(
            "This key can write to your personal library. catena does not need "
            "that: it deposits into a dedicated group. Turn off “Allow write "
            "access” for Personal Library."
        )
    if scope.groups_all_write:
        scope.reasons.append(
            "“All Groups” is set to Read/Write. The permissions you see on the "
            "current groups are exceptions to that default, so every new group "
            "will be born writable by this key. Set All Groups to Read Only and "
            "grant write access to the deposit group alone."
        )
    if scope.reasons:
        scope.verdict = WIDE
        return scope

    if not scope.writable_groups:
        scope.verdict = NARROW
        scope.reasons.append(
            "No group is writable, so catena has nowhere to deposit new items. "
            "Grant Read/Write to the deposit group only."
        )
    return scope


def check(api_key: str) -> KeyScope:
    return evaluate(fetch_current_key(api_key))
