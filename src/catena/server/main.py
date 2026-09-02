"""
catena — the web app, and the mount point of the MCP surface.

Two ways in. The browser gets a few pages: sign in, configure your Zotero key,
manage your MCP keys, look at your bindings. The model gets **/mcp**, gated by a
per-user X-API-Key, with the /mcp/k/{key} capability-URL variant for clients
that cannot set headers. Both resolve to a person, and a person reaches exactly
what their Zotero key reaches.

The page that matters is `/profile`. It is there that a Zotero key is validated
before being accepted (SPEC §2.2), and it is the only point in the system where
a wrong perimeter can still be stopped before it does any damage.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.orm import Session

from . import zotero_key
from .auth import (
    COOKIE,
    check_api_key,
    create_token,
    gateway_mode,
    get_current_user,
    hash_password,
    set_caller,
    verify_password,
)
from .mcp_app import mcp
from .models import (
    ApiKey, Binding, SessionLocal, User, ZoteroCredential, get_db, init_db, utcnow,
)

HERE = Path(__file__).parent
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://catena.borant.eu")

# The paths that reach the app without an identity, declared once here and read
# from here — `caddy.py` prints the reverse-proxy block from this list. Written
# down in one place because the day somebody adds a public route is the day they
# should notice, not six months later.
#
# The test a path has to pass is not "is it harmless to read": it is **no method
# on this path needs to know who is asking**. A public page with a private POST
# on top of it is the version of this mistake that people who learned the first
# version still make.
PUBLIC_PATHS = [
    "/",             # the showcase, which never looks at its reader
    "/healthz",
    "/static/*",
    "/login",        # in gateway mode the app turns these away itself
    "/logout",
    "/mcp",          # its own per-user key; a model client has no cookie
    "/mcp/*",
]

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="catena", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def _allowed_hosts() -> list[str]:
    hosts = ["localhost:8022", "127.0.0.1:8022", "localhost", "127.0.0.1"]
    public = urlparse(PUBLIC_URL).netloc
    if public:
        hosts.append(public)
    return hosts


app.mount(
    "/mcp",
    mcp.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=_allowed_hosts(), allowed_origins=[PUBLIC_URL]
        ),
    ),
)


@app.middleware("http")
async def api_key_gate(request: Request, call_next):
    """Resolve the MCP caller, or refuse.

    Two ways in, one table. The header is the normal path; /mcp/k/{key} carries
    the same key as a path segment for clients that cannot set headers, and is
    stripped before the mounted app sees it — so the MCP layer never learns how
    the caller authenticated.

    Deliberately independent of AUTH_MODE: a model client has no browser, no
    cookie and no gate session, so its key is the only credential it can carry.
    """
    path = request.url.path
    if not path.startswith("/mcp"):
        return await call_next(request)

    # The endpoint people are told to use is the one without a trailing slash,
    # and the Starlette mount answers that with a 307. MCP clients do not follow
    # redirects on POST, and behind TLS termination it is worse: the app does not
    # know it is on https, so the redirect it builds points at http://. Normalise
    # here rather than advertising a URL that only works with the slash — which
    # is how it gets tested by whoever just wrote it.
    if path == "/mcp":
        path = "/mcp/"
        request.scope["path"] = path
        request.scope["raw_path"] = path.encode()

    # Two ways to present the key, and the path variant is stripped before the
    # mounted app sees it, so the MCP layer never learns how the caller
    # authenticated.
    if path.startswith("/mcp/k/"):
        key, _, rest = path[len("/mcp/k/") :].partition("/")
        request.scope["path"] = "/mcp/" + rest
        request.scope["raw_path"] = request.scope["path"].encode()
    else:
        key = request.headers.get("X-API-Key", "")

    db = SessionLocal()
    try:
        row = check_api_key(db, key)
        set_caller(row.user if row and row.user.is_active else None)
        ok = bool(row and row.user.is_active)
    finally:
        db.close()
    if not ok:
        return JSONResponse({"error": "missing or invalid API key"}, status_code=401)
    return await call_next(request)


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("gateway", gateway_mode())
    return templates.TemplateResponse(request, name, ctx)


@app.exception_handler(HTTPException)
def _http_error(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        # Fail closed, do not bounce. In gateway mode this app turns /login away
        # itself, so redirecting there would put the two in a loop that nobody
        # sees in production — the gate intercepts first — and that spins
        # forever the day a Caddy matcher is wrong. A request with no identity
        # here means the gate did not run, which is a configuration fault, so
        # the answer is a 503 addressed to whoever can fix it.
        if gateway_mode():
            return templates.TemplateResponse(
                request,
                "error.html",
                {
                    "code": 503,
                    "detail": (
                        "This request arrived without an identity from the "
                        "sign-in gate. That means the gate did not run in front "
                        "of it — check that the Caddy block for this host "
                        "imports borantid, and that BORANT_TRUSTED_PROXY matches "
                        "the address requests actually come from."
                    ),
                    "gateway": True,
                },
                status_code=503,
            )
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "error.html",
        {"code": exc.status_code, "detail": exc.detail, "gateway": gateway_mode()},
        status_code=exc.status_code,
    )


# --- session (local mode only) -----------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if gateway_mode():
        return render(request, "gated.html")
    return render(request, "login.html")


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if gateway_mode():
        raise HTTPException(404, "Not found")
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return render(request, "login.html", error="Wrong email or password.")
    resp = RedirectResponse("/app", status_code=303)
    resp.set_cookie(
        COOKIE,
        create_token(user.id),
        httponly=True,
        samesite="lax",
        secure=PUBLIC_URL.startswith("https"),
        max_age=7 * 24 * 3600,
    )
    return resp


@app.get("/logout")
def logout():
    if gateway_mode():
        # The session belongs to the gate, not to us: ending it here would
        # leave the browser holding a live gate cookie and looking signed out.
        return RedirectResponse("https://id.borant.eu/logout", status_code=303)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


# --- the showcase, and the app behind it -------------------------------------


@app.get("/", response_class=HTMLResponse)
def showcase(request: Request):
    """A public page that never looks at who is reading it.

    Not looking is the whole point. On the gated branch the identity headers are
    stripped by construction, so a page that checks would be always-anonymous
    behind the gate and sometimes-not without it — the same page with two
    behaviours. Not checking, it renders identically in both modes, and a single
    button covers all four cases: gated or standalone, already signed in or not.

    The button points at `/app`, which is gated, and never at `/login`: a page
    that cannot recognise anybody, with a button that leads back to itself, is a
    ring with no way in.
    """
    return render(request, "showcase.html")


@app.get("/app", response_class=HTMLResponse)
def home(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bindings = (
        db.query(Binding)
        .filter(Binding.user_id == user.id)
        .order_by(Binding.updated_at.desc())
        .all()
    )
    cred = db.query(ZoteroCredential).filter(ZoteroCredential.user_id == user.id).first()
    return render(request, "app.html", user=user, bindings=bindings, cred=cred)


# --- profile: the Zotero key and the MCP keys --------------------------------


def _profile_ctx(db: Session, user: User, **extra) -> dict:
    keys = (
        db.query(ApiKey)
        .filter(ApiKey.user_id == user.id)
        .order_by(ApiKey.active.desc(), ApiKey.created_at.desc())
        .all()
    )
    cred = db.query(ZoteroCredential).filter(ZoteroCredential.user_id == user.id).first()
    scope = None
    if cred and cred.scope_json:
        try:
            scope = zotero_key.evaluate(json.loads(cred.scope_json))
        except (json.JSONDecodeError, TypeError):
            scope = None
    return {
        "user": user,
        "keys": keys,
        "cred": cred,
        "scope": scope,
        "public_url": PUBLIC_URL,
        **extra,
    }


@app.get("/profile", response_class=HTMLResponse)
def profile(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return render(request, "profile.html", **_profile_ctx(db, user))


@app.post("/profile/zotero", response_class=HTMLResponse)
def save_zotero_key(
    request: Request,
    api_key: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Validate first, save second. A key with the wrong perimeter never lands."""
    api_key = api_key.strip()
    try:
        scope = zotero_key.check(api_key)
    except zotero_key.ZoteroError as e:
        return render(request, "profile.html", **_profile_ctx(db, user, error=str(e)))

    if not scope.usable:
        return render(request, "profile.html", **_profile_ctx(db, user, rejected=scope))

    cred = db.query(ZoteroCredential).filter(ZoteroCredential.user_id == user.id).first()
    if not cred:
        cred = ZoteroCredential(user_id=user.id)
        db.add(cred)
    cred.key = api_key
    cred.zotero_user_id = scope.user_id
    cred.zotero_username = scope.username
    cred.scope_json = scope.raw_json
    cred.verdict = scope.verdict
    cred.checked_at = utcnow()
    db.commit()
    return render(
        request,
        "profile.html",
        **_profile_ctx(db, user, ok="Zotero key verified and saved."),
    )


@app.post("/profile/zotero/forget")
def forget_zotero_key(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    db.query(ZoteroCredential).filter(ZoteroCredential.user_id == user.id).delete()
    db.commit()
    return RedirectResponse("/profile", status_code=303)


@app.post("/profile/keys")
def new_api_key(
    name: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.add(ApiKey(user_id=user.id, name=name.strip() or "unnamed"))
    db.commit()
    return RedirectResponse("/profile", status_code=303)


@app.post("/profile/keys/{key_id}/revoke")
def revoke_api_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.user_id == user.id).first()
    if not row:
        raise HTTPException(404, "Key not found")
    row.active = False
    db.commit()
    return RedirectResponse("/profile", status_code=303)


@app.post("/profile/password")
def change_password(
    request: Request,
    current: str = Form(...),
    new: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if gateway_mode():
        # The password is the gate's business. Changing it here would edit a
        # credential nobody uses and leave the real one untouched.
        raise HTTPException(404, "Not found")
    if not verify_password(current, user.password_hash):
        return render(
            request,
            "profile.html",
            **_profile_ctx(db, user, error="Current password is not correct."),
        )
    user.password_hash = hash_password(new)
    db.commit()
    return render(
        request, "profile.html", **_profile_ctx(db, user, ok="Password updated.")
    )


@app.get("/healthz")
def healthz():
    """Deliberately outside the gate — and therefore green even when the gate is
    down and nobody can get in. Any useful monitor also hits a gated route."""
    return {"ok": True, "auth_mode": "gateway" if gateway_mode() else "local"}
