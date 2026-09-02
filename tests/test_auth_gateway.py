"""
The two authentication modes — SPEC §2 and the Borant ID contract.

The test that matters most is the first one. `AUTH_MODE=local` is the default
precisely so that an app deployed without a gate in front of it does not believe
an identity header, and that is a property worth pinning down: it is invisible
when it works and catastrophic when it breaks.

The second one pins the other half. Even in gateway mode the headers are only
believed when they arrive from the reverse proxy, because on a container that
gets exposed by accident anyone can send them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from catena.server import auth
from catena.server.models import SessionLocal, User, ZoteroCredential, init_db

init_db()


class FakeRequest:
    """Just enough of a Request: case-insensitive headers and a peer address."""

    def __init__(self, headers: dict[str, str], peer: str | None = "127.0.0.1"):
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.client = SimpleNamespace(host=peer) if peer else None


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_MODE", "gateway")


GATE_HEADERS = {
    "X-Borant-Sub": "01SUBJECT",
    "X-Borant-Email": "Someone@Example.Org",
    "X-Borant-Name": "Someone",
}


def test_local_mode_ignores_identity_headers(db, monkeypatch):
    """The whole reason `local` is the default: headers mean nothing here."""
    monkeypatch.setattr(auth, "AUTH_MODE", "local")
    assert auth.user_from_gateway(FakeRequest(GATE_HEADERS), db) is None


def test_gateway_ignores_headers_from_an_untrusted_peer(db, gateway, monkeypatch):
    monkeypatch.setattr(auth, "TRUSTED_PROXIES", auth._parse_trusted("127.0.0.1"))
    req = FakeRequest(GATE_HEADERS, peer="203.0.113.9")
    assert auth.user_from_gateway(req, db) is None


def test_gateway_provisions_a_profile_that_can_see_nothing(db, gateway, monkeypatch):
    """A fresh profile is harmless: no Zotero credential means no library."""
    monkeypatch.setattr(auth, "TRUSTED_PROXIES", auth._parse_trusted("127.0.0.1"))
    headers = dict(GATE_HEADERS, **{"X-Borant-Sub": "01FRESH"})

    user = auth.user_from_gateway(FakeRequest(headers), db)
    assert user is not None
    assert user.borant_sub == "01FRESH"
    assert user.email == "someone@example.org", "the email is normalised"
    assert not user.is_admin

    cred = (
        db.query(ZoteroCredential).filter(ZoteroCredential.user_id == user.id).first()
    )
    assert cred is None, "a new profile reaches no library at all"


def test_gateway_looks_up_by_sub_and_never_by_email(db, gateway, monkeypatch):
    """A typo in the gate's admin panel must not hand over someone's key."""
    monkeypatch.setattr(auth, "TRUSTED_PROXIES", auth._parse_trusted("127.0.0.1"))
    existing = User(
        email="taken@example.org",
        name="Taken",
        password_hash=auth.hash_password("x"),
        borant_sub="01MINE",
    )
    db.add(existing)
    db.commit()

    # Same email, different subject: this must NOT resolve to `existing`.
    other = auth.user_from_gateway(
        FakeRequest({"X-Borant-Sub": "01OTHER", "X-Borant-Email": "taken@example.org"}),
        db,
    )
    assert other is not None
    assert other.id != existing.id
    assert other.email != existing.email, "the address must not be stolen either"

    # Same subject: this must.
    same = auth.user_from_gateway(FakeRequest({"X-Borant-Sub": "01MINE"}), db)
    assert same is not None and same.id == existing.id


def test_an_email_already_taken_does_not_lock_anyone_out(db, gateway, monkeypatch):
    """The day the gate is switched on over an installation with local accounts.

    A brand-new subject arrives carrying the address of an existing row, and
    `users.email` is unique. Crashing here would lock that person out of the app
    altogether, so the profile is created under a non-colliding address and
    `link_borant.py` joins the two by hand.
    """
    monkeypatch.setattr(auth, "TRUSTED_PROXIES", auth._parse_trusted("127.0.0.1"))
    local_only = User(
        email="legacy@example.org",
        name="Legacy",
        password_hash=auth.hash_password("x"),
    )
    db.add(local_only)
    db.commit()

    fresh = auth.user_from_gateway(
        FakeRequest(
            {"X-Borant-Sub": "01LEGACY", "X-Borant-Email": "legacy@example.org"}
        ),
        db,
    )
    assert fresh is not None, "no crash, and the person still gets in"
    assert fresh.id != local_only.id
    assert fresh.email == "01legacy@borant.invalid"
    assert local_only.borant_sub is None, "the old account is left untouched"


def test_admin_hint_is_honoured_and_a_typo_is_not(db, gateway, monkeypatch):
    monkeypatch.setattr(auth, "TRUSTED_PROXIES", auth._parse_trusted("127.0.0.1"))
    boss = auth.user_from_gateway(
        FakeRequest({"X-Borant-Sub": "01BOSS", "X-Borant-Hint": "admin"}), db
    )
    assert boss.is_admin is True

    typo = auth.user_from_gateway(
        FakeRequest({"X-Borant-Sub": "01TYPO", "X-Borant-Hint": "administrator"}), db
    )
    assert typo.is_admin is False, "an unknown hint is a typo, not a role"


def test_no_sub_header_is_simply_nobody(db, gateway):
    assert auth.user_from_gateway(FakeRequest({}), db) is None


def test_trusted_proxy_accepts_a_cidr_and_drops_nonsense():
    nets = auth._parse_trusted("172.17.0.0/16, not-an-address ,10.0.0.1")
    assert len(nets) == 2
    req = FakeRequest({}, peer="172.17.0.1")
    assert any(
        __import__("ipaddress").ip_address("172.17.0.1") in n for n in nets
    ), "a container bridge range must be expressible"
    assert req.client.host == "172.17.0.1"
