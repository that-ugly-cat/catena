"""
Server state: people, credentials, bindings.

Two kinds of key live here and they must not be confused.

`ApiKey` is a credential **towards** catena: an MCP client presents it to be
recognised, and it carries a person's identity. `ZoteroCredential` is a person's
credential **towards Zotero**, which catena uses on their behalf. The first is
revoked here and it is gone; the second is revoked on zotero.org, and all we can
do here is forget it.

The binding model follows SPEC §3: two legs, one read from and one written to,
plus an `ingest_event` that is at once a log and the idempotency key (§9.1).

Migrations are additive and run at startup, as in the rest of the borant tools:
ALTER TABLE for each new column, never a rename and never a drop.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

DB_PATH = os.environ.get("CATENA_DB", "data/catena.db")
engine = create_engine(
    f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    # Borant ID subject, set when a profile arrives through the gate. Lookup is
    # by this and never by email: a typo in the gate's admin panel must not be
    # able to hand one person another person's Zotero credential.
    borant_sub = Column(String, unique=True, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class ApiKey(Base):
    """An MCP credential, and deliberately a credential *of a person*.

    Every MCP call resolves to a user, and from there the reach is whatever that
    user's Zotero key allows — nothing more. Without the binding to a person the
    MCP surface would be a hole straight through the access model.
    """

    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    key = Column(
        String,
        unique=True,
        nullable=False,
        default=lambda: "cat_" + secrets.token_urlsafe(32),
    )
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)
    last_used_at = Column(DateTime, nullable=True)

    user = relationship("User")


class ZoteroCredential(Base):
    """A person's Zotero key, plus the verdict on its perimeter.

    `key` is a third party's secret held in clear text: whoever reads the
    database reads the key. That is only acceptable because the perimeter is
    narrowed on the way in (SPEC §2.2) — a key that can write neither to the
    personal library nor to arbitrary groups does limited damage if it escapes.
    It is not an excuse to leave it lying around: the interface never shows it
    whole and never sends it back to the browser.

    `scope_json` is the GET /keys/current response as it stood when the key was
    saved, kept so the app can still explain *why* a key was accepted or
    refused months later.
    """

    __tablename__ = "zotero_credentials"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    key = Column(String, nullable=False)
    zotero_user_id = Column(String, nullable=True)
    zotero_username = Column(String, nullable=True)
    scope_json = Column(Text, nullable=True)
    verdict = Column(String, nullable=False)  # ok | narrow | wide
    checked_at = Column(DateTime, default=utcnow)

    user = relationship("User")

    @property
    def masked(self) -> str:
        return "…" + self.key[-4:] if self.key else ""


class Binding(Base):
    """One paper, one collection. Two legs: read from one, write to the other."""

    __tablename__ = "bindings"
    __table_args__ = (UniqueConstraint("user_id", "label"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    label = Column(String, nullable=False)
    source_library = Column(String, nullable=False)
    source_collection_key = Column(String, nullable=True)
    deposit_library = Column(String, nullable=False)
    deposit_collection_key = Column(String, nullable=True)
    papertrail_project_id = Column(String, nullable=True)
    csl_style = Column(String, default="http://www.zotero.org/styles/vancouver")
    locale = Column(String, default="en-GB")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User")

    @property
    def style_name(self) -> str:
        return (self.csl_style or "").rsplit("/", 1)[-1]

    @property
    def one_legged(self) -> bool:
        return self.source_library == self.deposit_library


class IngestEvent(Base):
    """The log of writes towards Zotero, and the idempotency key (SPEC §9.1).

    The unique constraint is the real protection against duplicates on retry.
    pyzotero sends a fresh Zotero-Write-Token on every invocation, so it covers
    its own internal retries but not a second call from the caller. This row
    does, because it lives at the level where that retry happens.
    """

    __tablename__ = "ingest_events"
    __table_args__ = (UniqueConstraint("binding_id", "identifier"),)
    id = Column(Integer, primary_key=True)
    binding_id = Column(Integer, ForeignKey("bindings.id"), nullable=False)
    identifier = Column(String, nullable=False)
    identifier_kind = Column(String, nullable=False)  # doi | pmid | arxiv | isbn | url
    item_key = Column(String, nullable=True)
    item_library = Column(String, nullable=True)
    source = Column(String, nullable=False)  # manual | contrarian
    run_id = Column(String, nullable=True)
    verdict = Column(String, nullable=True)
    promoted_to_key = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    binding = relationship("Binding")


class IngestPlan(Base):
    """What an ingest would do, decided before anything is written.

    A plan is persisted rather than returned and forgotten, so that
    `apply_ingest` executes what a person actually read and agreed to — not a
    fresh resolution that may have drifted. It also carries the second half of
    the idempotency: a plan already applied is refused instead of replayed.
    """

    __tablename__ = "ingest_plans"
    id = Column(Integer, primary_key=True)
    binding_id = Column(Integer, ForeignKey("bindings.id"), nullable=False)
    payload = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    applied_at = Column(DateTime, nullable=True)

    binding = relationship("Binding")


# Columns added after the first release. Additive only: the migration runs an
# ALTER TABLE when the column is missing and does nothing when it is there.
_ADDED_COLUMNS = {
    "users": {"borant_sub": "VARCHAR"},
}


def _migrate() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    Base.metadata.create_all(engine)
    _migrate()
