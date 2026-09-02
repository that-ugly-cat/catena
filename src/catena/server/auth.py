"""
Authentication — the borant house pattern, in both of its modes.

Two ways of recognising a person, and `local` is the default on purpose: an app
that believes an identity header with nothing in front of it lets in anyone who
can send that header. The gateway path stays dead code until someone turns it
on deliberately.

    local     email + password against the users table
    gateway   an upstream Borant ID gate vouches for the caller via X-Borant-*

Note what does not change between them. The MCP surface keeps its own per-user
API key, because a model client has no browser and no cookie; and Borant ID says
*who you are* while this app decides *what you may reach*. Here that decision is
made almost entirely outside catena: a profile without a Zotero credential sees
no library at all, so a freshly provisioned user lands on an empty screen rather
than on someone else's references.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets
from contextvars import ContextVar
from datetime import datetime, timedelta

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .models import ApiKey, User, get_db, utcnow

log = logging.getLogger("catena.auth")

SECRET_KEY = os.environ["JWT_SECRET"]
ALGORITHM = "HS256"
EXPIRE_DAYS = 7
COOKIE = "session"

AUTH_MODE = os.environ.get("AUTH_MODE", "local").strip().lower()

# In gateway mode identity headers are believed only when they arrive from here
# — the reverse proxy, never the internet. Under Docker this is the bridge
# gateway address and NOT 127.0.0.1; DEPLOY.md shows how to read it off a
# running container.
TRUSTED_PROXY = os.environ.get("BORANT_TRUSTED_PROXY", "127.0.0.1")


def _parse_trusted(raw: str) -> list:
    nets = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            nets.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            log.warning(
                "BORANT_TRUSTED_PROXY: ignoring %r, not an address or CIDR", chunk
            )
    return nets


TRUSTED_PROXIES = _parse_trusted(TRUSTED_PROXY)


def gateway_mode() -> bool:
    return AUTH_MODE == "gateway"


def _from_trusted_proxy(request: Request) -> bool:
    peer = request.client.host if request.client else None
    if not peer:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in TRUSTED_PROXIES)


def user_from_gateway(request: Request, db: Session) -> User | None:
    """The user the gate vouched for, or None.

    Lookup is by `borant_sub` and never by email: a typo in the gate's admin
    panel must not hand one person another person's Zotero credential.

    An unknown subject gets a fresh profile. That is harmless here in a way
    worth stating, because it is the whole reason auto-provisioning is
    acceptable: a new profile has no Zotero credential and no bindings, so it
    can read nothing from anyone's library. The failure mode is an empty page,
    not a leak.
    """
    if not gateway_mode():
        return None
    sub = request.headers.get("x-borant-sub")
    if not sub:
        return None
    if not _from_trusted_proxy(request):
        log.warning(
            "X-Borant-Sub from %s, outside BORANT_TRUSTED_PROXY (%s): ignored",
            request.client.host if request.client else "?",
            TRUSTED_PROXY,
        )
        return None

    user = db.query(User).filter(User.borant_sub == sub).first()
    if user is not None:
        return user if user.is_active else None

    email = (
        request.headers.get("x-borant-email", "") or f"{sub}@borant.invalid"
    ).strip().lower()

    # The day the gate is switched on over an installation that already has
    # local accounts, the first thing that happens is a collision: a brand-new
    # subject arrives carrying the email of an existing row, and `users.email`
    # is unique. Crashing here would lock that person out of the app entirely.
    #
    # We do not adopt the existing row either. Linking by email is the one thing
    # this function refuses to do, because an email is something a gate operator
    # types and a subject is not — and adopting on a typo would hand over the
    # account together with its Zotero credential. So: a fresh profile under a
    # non-colliding address, loudly, and `link_borant.py` joins the two by hand.
    if db.query(User).filter(User.email == email).first() is not None:
        placeholder = f"{sub}@borant.invalid".lower()
        log.warning(
            "gateway: %s already belongs to a local account with no borant_sub. "
            "Created a separate profile as %s rather than adopting it; run "
            "link_borant.py to join them.",
            email,
            placeholder,
        )
        email = placeholder

    # An unguessable local password rather than none: `AUTH_MODE=local` has to
    # remain a working way back in when the gate is down, and a row with no
    # password is not a way back.
    #
    # X-Borant-Hint is deliberately ignored, and no role vocabulary is declared
    # for this app in the gate. A vocabulary is a contract about what the code
    # actually reads: catena has no admin surface, so honouring `admin` here
    # would set a flag that opens nothing — the same silent mismatch that had
    # one tool offering a role its code quietly downgraded. If an admin surface
    # ever exists, the vocabulary gets declared in the same commit that reads it.
    hint = (request.headers.get("x-borant-hint", "") or "").strip()
    if hint:
        log.info("gateway: hint %r ignored — catena declares no roles", hint)

    user = User(
        email=email,
        name=request.headers.get("x-borant-name", "") or email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        borant_sub=sub,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log.info("gateway: new profile for %s (%s)", email, sub)
    return user


# --- passwords and tokens (local mode) ---------------------------------------


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(days=EXPIRE_DAYS)
    return jwt.encode(
        {"sub": str(user_id), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM
    )


def _decode(token: str) -> int:
    try:
        return int(jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")


# --- who is asking -----------------------------------------------------------


def get_current_user(
    request: Request,
    session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if gateway_mode():
        user = user_from_gateway(request, db)
        if not user:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
        return user

    if not session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = (
        db.query(User)
        .filter(User.id == _decode(session), User.is_active.is_(True))
        .first()
    )
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


def get_user_or_none(
    session: str | None, db: Session, request: Request | None = None
) -> User | None:
    """Plain function, not a Depends: for pages that also render logged out."""
    if gateway_mode():
        return user_from_gateway(request, db) if request is not None else None
    if not session:
        return None
    try:
        uid = _decode(session)
    except HTTPException:
        return None
    return db.query(User).filter(User.id == uid, User.is_active.is_(True)).first()


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin required")
    return user


# --- the MCP surface ---------------------------------------------------------


def check_api_key(db: Session, key: str) -> ApiKey | None:
    """The active row for this key, stamping its use.

    `last_used_at` earns its keep in one place only, but a useful one: telling a
    live key from a forgotten one when deciding what to revoke.
    """
    row = db.query(ApiKey).filter(ApiKey.key == key, ApiKey.active.is_(True)).first()
    if row:
        row.last_used_at = utcnow()
        db.commit()
    return row


_caller: ContextVar["User | None"] = ContextVar("mcp_caller", default=None)


def set_caller(user: "User | None") -> None:
    """Called once per MCP request, by the middleware that resolved the key."""
    _caller.set(user)


def current_caller() -> User:
    """The person this MCP call runs as.

    A ContextVar rather than a parameter threaded through every tool: the MCP
    layer does not carry request objects into tool functions, and an identity
    that has to be passed by hand is an identity somebody eventually forgets to
    pass.
    """
    user = _caller.get()
    if user is None:
        raise PermissionError("no MCP caller in context — the key gate did not run")
    return user


def mcp_user(db: Session, request: Request, path_key: str | None = None) -> User:
    """MCP callers authenticate with their own key in both auth modes.

    Deliberately independent of AUTH_MODE: a model client has no browser, no
    cookie and no gate session, so the key is the only credential it can carry.
    """
    key = path_key or request.headers.get("X-API-Key")
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing MCP key")
    row = check_api_key(db, key)
    if not row or not row.user or not row.user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid MCP key")
    return row.user
