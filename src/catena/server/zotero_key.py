"""
Validazione della chiave Zotero — SPEC §2.2.

Una chiave Zotero non e' accettabile solo perche' funziona: deve avere il
perimetro giusto. Il caso che questa funzione esiste per intercettare e' subdolo
e capita davvero — e' capitato durante la scrittura della specifica, il
2 settembre 2026.

`access.groups` puo' contenere una voce `all` che vale da default per i gruppi
non elencati esplicitamente. Con `all.write = true`, i `write: false` sui gruppi
esistenti sembrano un perimetro stretto, ma **ogni gruppo creato o raggiunto in
futuro nasce scrivibile**. Il perimetro non e' fisso: cresce da solo, e sei mesi
dopo nessuno se lo ricorda.

Il verdetto:

    ok       il default nega, l'eccezione e' esplicita e limitata
    larga    scrittura sulla libreria personale, o groups.all.write -> RIFIUTATA
    stretta  nessun gruppo scrivibile: catena non ha dove depositare
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API = "https://api.zotero.org"
TIMEOUT = 15

OK, LARGA, STRETTA = "ok", "larga", "stretta"


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
                "Zotero non riconosce questa chiave (403). Controlla di averla "
                "copiata per intero da zotero.org/settings/keys."
            ) from e
        raise ZoteroError(f"Zotero ha risposto {e.code}.") from e
    except urllib.error.URLError as e:
        raise ZoteroError(f"Non riesco a raggiungere api.zotero.org: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise ZoteroError("Risposta di Zotero illeggibile.") from e


def evaluate(payload: dict) -> KeyScope:
    """Il verdetto sul perimetro, con le ragioni in chiaro."""
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
            "La chiave puo' scrivere sulla tua libreria personale. catena non ne "
            "ha bisogno: deposita in un gruppo dedicato. Togli «Allow write "
            "access» da Personal Library."
        )
    if scope.groups_all_write:
        scope.reasons.append(
            "«All Groups» e' impostato su Read/Write. I permessi che vedi sui "
            "gruppi attuali sono eccezioni a quel default, quindi ogni gruppo "
            "nuovo nascera' scrivibile da questa chiave. Metti All Groups su "
            "Read Only e concedi la scrittura solo al gruppo di deposito."
        )
    if scope.reasons:
        scope.verdict = LARGA
        return scope

    if not scope.writable_groups:
        scope.verdict = STRETTA
        scope.reasons.append(
            "Nessun gruppo e' scrivibile, quindi catena non ha dove depositare "
            "gli item nuovi. Concedi Read/Write al solo gruppo di deposito."
        )
    return scope


def check(api_key: str) -> KeyScope:
    return evaluate(fetch_current_key(api_key))
