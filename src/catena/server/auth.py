"""
Autenticazione — pattern di casa, lo stesso di PaperTrail e LSSR.

JWT in un cookie httpOnly chiamato `session`, sette giorni, segreto da
`JWT_SECRET` (l'avvio fallisce se manca: un segreto di default e' peggio di
nessun segreto).

Le chiavi MCP viaggiano nell'header `X-API-Key`, con la variante nel path per i
client che non sanno mandare header custom. Ogni chiave e' legata a una persona,
e da li' in poi la portata e' quella della sua chiave Zotero: catena non ha
credenziali proprie verso Zotero, quindi non ha modo di superare il perimetro
del suo utente nemmeno volendo.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .models import ApiKey, User, get_db, utcnow

SECRET_KEY = os.environ["JWT_SECRET"]
ALGORITHM = "HS256"
EXPIRE_DAYS = 7
COOKIE = "session"


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
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessione non valida")


def get_current_user(
    session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Non autenticato")
    user = (
        db.query(User)
        .filter(User.id == _decode(session), User.is_active.is_(True))
        .first()
    )
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utente non trovato")
    return user


def get_user_or_none(session: str | None, db: Session) -> User | None:
    """Funzione semplice, non una Depends: per le pagine che si disegnano anche
    da sloggati."""
    if not session:
        return None
    try:
        uid = _decode(session)
    except HTTPException:
        return None
    return db.query(User).filter(User.id == uid, User.is_active.is_(True)).first()


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Serve un amministratore")
    return user


# --- superficie MCP ----------------------------------------------------------


def check_api_key(db: Session, key: str) -> ApiKey | None:
    """La riga attiva per questa chiave, marcandone l'uso.

    `last_used_at` serve a una cosa sola ma utile: distinguere una chiave viva
    da una dimenticata, quando si decide cosa revocare.
    """
    row = db.query(ApiKey).filter(ApiKey.key == key, ApiKey.active.is_(True)).first()
    if row:
        row.last_used_at = utcnow()
        db.commit()
    return row


def mcp_user(db: Session, request: Request, path_key: str | None = None) -> User:
    key = path_key or request.headers.get("X-API-Key")
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Chiave MCP assente")
    row = check_api_key(db, key)
    if not row or not row.user or not row.user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Chiave MCP non valida")
    return row.user
