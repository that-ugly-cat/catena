"""
The Zotero key perimeter validator — SPEC §2.2.

These tests never touch the network: `evaluate()` judges an already-fetched
payload, and the payloads below are the shapes that matter. The first one is the
real trap, and it happened for real on 2 September 2026: a key that *looks*
narrow because every existing group is read-only, while the `all` default grants
write access — so every future group will be born writable.
"""

from __future__ import annotations

from catena.server.zotero_key import NARROW, OK, WIDE, evaluate


def payload(user: dict, groups: dict) -> dict:
    return {
        "key": "xxxx",
        "userID": 5333547,
        "username": "giovanni.spitale",
        "access": {"user": user, "groups": groups},
    }


def test_all_write_is_not_rescued_by_per_group_overrides():
    """The trap: ten read-only groups and a default that grants."""
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
    assert scope.verdict == WIDE
    assert scope.groups_all_write is True
    assert any("All Groups" in r for r in scope.reasons)


def test_write_on_the_personal_library_is_refused():
    scope = evaluate(
        payload(
            {"library": True, "files": True, "write": True},
            {"all": {"library": True, "write": False}, "999": {"library": True, "write": True}},
        )
    )
    assert scope.verdict == WIDE
    assert scope.personal_write is True
    assert any("personal library" in r for r in scope.reasons)


def test_no_writable_group_is_too_narrow():
    """With no deposit group catena has nowhere to put new items."""
    scope = evaluate(
        payload(
            {"library": True},
            {"all": {"library": True, "write": False}, "111": {"library": True, "write": False}},
        )
    )
    assert scope.verdict == NARROW
    assert not scope.usable


def test_the_correct_shape_is_accepted():
    """The default denies, the exception is single and explicit."""
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


def test_two_problems_give_two_reasons():
    scope = evaluate(
        payload(
            {"library": True, "write": True},
            {"all": {"library": True, "write": True}},
        )
    )
    assert scope.verdict == WIDE
    assert len(scope.reasons) == 2
