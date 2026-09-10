"""The browser route for `inject`: same function as the CLI, nothing retained.

The command line stays the right default — on a laptop the manuscript never
leaves the machine — but it needs Python and a terminal. This route is for the
people who have neither, and the property it has to keep is the one that makes
it acceptable at all: the upload exists for the length of one request and is
gone with it.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from catena.server import auth
from catena.server.main import app
from catena.server.models import SessionLocal, User, init_db

init_db()

MARKER = "[@Rosato2008]"
FIELD = "ADDIN ZOTERO_ITEM CSL_CITATION {\"citationID\":\"abc\"}"

MINIMAL_DOCX_DOC = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>Some claim " + MARKER + " and the sentence goes on.</w:t></w:r></w:p>"
    "</w:body></w:document>"
)
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/></Types>'
)


def _docx_bytes(doc_xml: str = MINIMAL_DOCX_DOC) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


@pytest.fixture(scope="module")
def c():
    """A client that deliberately does NOT enter the app's lifespan.

    Entering it starts the MCP session manager, which refuses to run twice in
    one process — so a second test module that needs it (test_routes) would
    fail, and the failure would look like the app's rather than the suite's.
    This route needs neither MCP nor startup: only the database and a cookie.
    """
    client = TestClient(app, base_url="http://localhost:8022", follow_redirects=False)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "inject@example.org").first()
        if not user:
            user = User(email="inject@example.org", name="Inject",
                        password_hash=auth.hash_password("x"))
            db.add(user)
            db.commit()
            db.refresh(user)
        client.cookies.set(auth.COOKIE, auth.create_token(user.id))
    finally:
        db.close()
    return client


def _post(c, docx: bytes, plan: dict, filename="draft.docx"):
    return c.post(
        "/app/inject",
        files={"file": (filename, docx,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"plan": json.dumps(plan)},
    )


def test_places_the_field_and_returns_a_copy(c):
    r = _post(c, _docx_bytes(), {"fields": {MARKER: FIELD}})
    assert r.status_code == 200, r.text
    assert 'filename="draft-catena.docx"' in r.headers["content-disposition"]
    assert r.headers["X-Catena-Placed"] == "1"
    assert r.headers["X-Catena-Unmatched"] == ""
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        doc = z.read("word/document.xml").decode()
    assert "ZOTERO_ITEM" in doc, "the field code is not in the returned document"
    assert "fldChar" in doc, "the field was not built as a Word field"


def test_a_marker_not_in_the_plan_is_reported_and_left_alone(c):
    r = _post(c, _docx_bytes(), {"fields": {"[@Absent1999]": FIELD}})
    assert r.status_code == 200, r.text
    assert r.headers["X-Catena-Placed"] == "0"
    assert "[@Absent1999]" in r.headers["X-Catena-Unmatched"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        doc = z.read("word/document.xml").decode()
    assert MARKER in doc, "the author's own marker must survive untouched"


def test_nothing_is_left_on_disk(c, tmp_path, monkeypatch):
    """The upload and the output must not outlive the request."""
    import tempfile

    sandbox = tmp_path / "tmp"
    sandbox.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(sandbox))
    r = _post(c, _docx_bytes(), {"fields": {MARKER: FIELD}})
    assert r.status_code == 200, r.text
    assert list(sandbox.iterdir()) == [], f"left behind: {list(sandbox.iterdir())}"


def test_a_plan_that_is_not_json_is_refused(c):
    r = c.post(
        "/app/inject",
        files={"file": ("draft.docx", _docx_bytes(), "application/octet-stream")},
        data={"plan": "{not json"},
    )
    assert r.status_code == 400
    assert "not valid JSON" in r.json()["detail"]


def test_a_plan_with_no_fields_is_refused(c):
    r = _post(c, _docx_bytes(), {"fields": {}})
    assert r.status_code == 400
    assert "no fields" in r.json()["detail"]


def test_something_that_is_not_a_docx_is_refused(c):
    r = _post(c, b"this is not a zip at all", {"fields": {MARKER: FIELD}})
    assert r.status_code == 400


def test_an_empty_upload_is_refused(c):
    r = _post(c, b"", {"fields": {MARKER: FIELD}})
    assert r.status_code == 400


def test_the_route_needs_an_account(c):
    anon = TestClient(app, base_url="http://localhost:8022", follow_redirects=False)
    r = anon.post(
        "/app/inject",
        files={"file": ("draft.docx", _docx_bytes(), "application/octet-stream")},
        data={"plan": json.dumps({"fields": {MARKER: FIELD}})},
    )
    assert r.status_code in (302, 303, 401, 403, 503), r.status_code
