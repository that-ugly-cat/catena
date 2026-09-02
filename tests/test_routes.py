"""
The shape of the site — the house conventions, checked rather than remembered.

Every assertion here corresponds to a rule on the borant conventions page, and
each of those rules is there because it already cost somebody time. They are
worth testing precisely because none of them shows up in normal use: a showcase
that quietly checks the user, an endpoint advertised in the form that 307s, a
public path that turns out to need an identity. All of them work fine when you
try them the way you built them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from catena.server import auth
from catena.server.main import PUBLIC_PATHS, app
from catena.server.models import ApiKey, SessionLocal, User, init_db

init_db()

MCP_CALL = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_ACCEPT = "application/json, text/event-stream"


@pytest.fixture(scope="module")
def c():
    """One client for the whole module.

    The MCP session manager refuses to `.run()` twice on the same instance and
    the app's lifespan starts it, so a fresh TestClient per test fails after the
    first one. Entered once, shared.

    `base_url` is not decoration either: the MCP transport validates the Host
    header against DNS rebinding and refuses any name it was not told about.
    TestClient calls itself `testserver`, which earns a 421 — the same failure
    that shows up in production as "the tool is broken" when PUBLIC_URL is
    missing from the environment, and which never reproduces from the machine
    itself because 127.0.0.1 is always allowed.
    """
    with TestClient(app, base_url="http://localhost:8022", follow_redirects=False) as client:
        yield client


@pytest.fixture(scope="module")
def key() -> str:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "routes@example.org").first()
        if not user:
            user = User(
                email="routes@example.org",
                name="Routes",
                password_hash=auth.hash_password("x"),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        row = db.query(ApiKey).filter(ApiKey.user_id == user.id).first()
        if not row:
            row = ApiKey(user_id=user.id, name="test")
            db.add(row)
            db.commit()
            db.refresh(row)
        return row.key
    finally:
        db.close()


# --- the showcase --------------------------------------------------------------


def test_the_showcase_is_public_and_does_not_look_at_the_reader(c):
    """`/` renders identically in both auth modes because it never checks.

    A `{% if user %}` on a showcase is always false behind the gate, where the
    identity headers are stripped, and sometimes true without one — the same
    page with two behaviours.
    """
    r = c.get("/")
    assert r.status_code == 200
    assert "Enter" in r.text
    assert 'href="/app"' in r.text, "the button points at a gated path"
    assert 'href="/login"' not in r.text, (
        "never at /login: a page that cannot recognise anybody, with a button "
        "leading back to itself, is a ring with no way in"
    )


def test_the_app_itself_is_gated(c):
    for path in ("/app", "/profile"):
        assert c.get(path).status_code in (303, 401, 503), path


def test_healthz_is_public_and_says_which_mode_is_live(c):
    body = c.get("/healthz").json()
    assert body["ok"] is True
    assert body["auth_mode"] in ("local", "gateway")


# --- the MCP surface -----------------------------------------------------------


def test_the_advertised_endpoint_does_not_redirect(c, key):
    """`/mcp` without the trailing slash is what the profile page shows.

    The Starlette mount answers that with a 307; MCP clients do not follow
    redirects on POST, and behind TLS termination the redirect it builds points
    at http://. The trap is that it works with the slash, which is how whoever
    wrote it tries it.
    """
    r = c.post("/mcp", json=MCP_CALL, headers={"X-API-Key": key, "Accept": MCP_ACCEPT})
    assert r.status_code == 200, f"expected 200, got {r.status_code}"


def test_both_endpoint_forms_reach_the_same_place(c, key):
    headers = {"X-API-Key": key, "Accept": MCP_ACCEPT}
    assert c.post("/mcp", json=MCP_CALL, headers=headers).status_code == 200
    assert c.post("/mcp/", json=MCP_CALL, headers=headers).status_code == 200


def test_the_key_can_travel_in_the_path(c, key):
    """For clients that cannot set headers — with the key ending up in logs."""
    r = c.post(f"/mcp/k/{key}", json=MCP_CALL, headers={"Accept": MCP_ACCEPT})
    assert r.status_code == 200


def test_no_key_is_refused_with_a_message_of_our_own(c):
    """The 401 has to be read, not counted.

    Behind the gate an unauthenticated call also gets a 401 — from Borant ID,
    with a different body. Two refusals with the same number and opposite
    causes, so the proof is the body.
    """
    r = c.post("/mcp", json=MCP_CALL, headers={"Accept": MCP_ACCEPT})
    assert r.status_code == 401
    assert r.json() == {"error": "missing or invalid API key"}


# --- the declared list ---------------------------------------------------------


def test_every_public_path_is_declared_in_one_place():
    """`caddy.py` reads this list; nothing else should hold a second copy."""
    assert PUBLIC_PATHS[0] == "/", "the showcase leads"
    for needed in ("/healthz", "/static/*", "/mcp", "/mcp/*"):
        assert needed in PUBLIC_PATHS
    assert "/app" not in PUBLIC_PATHS and "/profile" not in PUBLIC_PATHS
