"""
catena — the web app.

A few pages, and for now none of them writes to Zotero: you sign in, you
configure your Zotero key, you manage your MCP keys, you look at your bindings.
The real operations arrive on the MCP surface.

The page that matters is `/profile`. It is there that a Zotero key is validated
before being accepted (SPEC §2.2), and it is the only point in the system where
a wrong perimeter can still be stopped before it does any damage.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import zotero_key
from .auth import (
    COOKIE,
    create_token,
    gateway_mode,
    get_current_user,
    hash_password,
    verify_password,
)
from .models import ApiKey, Binding, User, ZoteroCredential, get_db, init_db, utcnow

HERE = Path(__file__).parent
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://catena.borant.eu")

app = FastAPI(title="catena", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


@app.on_event("startup")
def _startup() -> None:
    init_db()


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("gateway", gateway_mode())
    return templates.TemplateResponse(request, name, ctx)


@app.exception_handler(HTTPException)
def _http_error(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        # In gateway mode the app has no sign-in of its own: the gate turns an
        # unauthenticated request away before it ever reaches here, so a 401
        # arriving at this point means the headers were missing or untrusted.
        # Sending the visitor to a local form we do not use would only confuse.
        if gateway_mode():
            return templates.TemplateResponse(
                request,
                "error.html",
                {"code": 401, "detail": "The sign-in gate did not vouch for this request.",
                 "gateway": True},
                status_code=401,
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
    resp = RedirectResponse("/", status_code=303)
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


# --- home --------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
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
    return render(request, "home.html", user=user, bindings=bindings, cred=cred)


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
