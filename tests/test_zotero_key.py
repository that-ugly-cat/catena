"""
Il validatore del perimetro della chiave Zotero — SPEC §2.2.

Questi test non toccano la rete: `evaluate()` giudica un payload gia' scaricato,
e i payload qui sotto sono le forme che contano. Il primo e' la trappola vera,
quella che e' capitata davvero il 2 settembre 2026: una chiave che *sembra*
ristretta perche' tutti i gruppi esistenti sono in sola lettura, mentre il
default `all` concede scrittura e quindi ogni gruppo futuro nascera' scrivibile.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catena.server.zotero_key import LARGA, OK, STRETTA, evaluate  # noqa: E402


def payload(user: dict, groups: dict) -> dict:
    return {
        "key": "xxxx",
        "userID": 5333547,
        "username": "giovanni.spitale",
        "access": {"user": user, "groups": groups},
    }


def test_all_write_non_e_salvato_dagli_override_per_gruppo():
    """La trappola: dieci gruppi in sola lettura e un default che concede."""
    scope = evaluate(
        payload(
            {"library": True, "files": True},
            {
                "all": {"library": True, "write": True},
                "111": {"library": True, "write": False},
                "222": {"library": True, "write": False},
                "999": {"library": True, "write": True},
            },
        )
    )
    assert scope.verdict == LARGA
    assert scope.groups_all_write is True
    assert any("All Groups" in r for r in scope.reasons)


def test_scrittura_sulla_libreria_personale_rifiutata():
    scope = evaluate(
        payload(
            {"library": True, "files": True, "write": True},
            {"all": {"library": True, "write": False}, "999": {"library": True, "write": True}},
        )
    )
    assert scope.verdict == LARGA
    assert scope.personal_write is True
    assert any("personale" in r for r in scope.reasons)


def test_nessun_gruppo_scrivibile_e_troppo_stretta():
    """Senza un gruppo di deposito catena non ha dove mettere gli item nuovi."""
    scope = evaluate(
        payload(
            {"library": True},
            {"all": {"library": True, "write": False}, "111": {"library": True, "write": False}},
        )
    )
    assert scope.verdict == STRETTA
    assert not scope.usable


def test_forma_corretta_accettata():
    """Il default nega, l'eccezione e' una sola ed esplicita."""
    scope = evaluate(
        payload(
            {"library": True, "files": True},
            {
                "all": {"library": True, "write": False},
                "111": {"library": True, "write": False},
                "222": {"library": True, "write": False},
                "6656239": {"library": True, "write": True},
            },
        )
    )
    assert scope.verdict == OK
    assert scope.usable
    assert scope.writable_groups == ["6656239"]
    assert scope.readable_groups == ["111", "222"]
    assert scope.user_id == "5333547"
    assert not scope.reasons


def test_due_problemi_danno_due_ragioni():
    scope = evaluate(
        payload(
            {"library": True, "write": True},
            {"all": {"library": True, "write": True}},
        )
    )
    assert scope.verdict == LARGA
    assert len(scope.reasons) == 2
