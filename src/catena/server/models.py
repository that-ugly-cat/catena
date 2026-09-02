"""
Lo stato del server: persone, credenziali, legami.

Due tipi di chiave vivono qui e non vanno confusi.

`ApiKey` e' una credenziale **verso** catena: la usa un client MCP per farsi
riconoscere, e porta l'identita' di una persona. `ZoteroCredential` e' una
credenziale **di** una persona verso Zotero: catena la usa per suo conto. La
prima si revoca da qui e sparisce; la seconda si revoca su zotero.org, e qui si
puo' solo dimenticare.

Il modello dei binding segue SPEC §3: due gambe, una da cui si legge e una su cui
si scrive, e un `ingest_event` che e' insieme registro e chiave di idempotenza
(§9.1).
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
    created_at = Column(DateTime, default=utcnow)


class ApiKey(Base):
    """Credenziale MCP, e deliberatamente credenziale *di una persona*.

    Ogni chiamata MCP si risolve in un utente, e da li' in poi vale quello che
    vale per lui: le librerie che la sua chiave Zotero vede, e nient'altro.
    Senza questo legame la superficie MCP sarebbe un buco dritto attraverso il
    modello di accesso.
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
    """La chiave Zotero di una persona, piu' il verdetto sul suo perimetro.

    Il campo `key` e' un segreto di terzi conservato in chiaro: chi legge il
    database legge la chiave. E' accettabile solo perche' il perimetro e'
    ristretto in ingresso (§2.2) — una chiave che non puo' scrivere sulla
    libreria personale ne' su gruppi arbitrari fa pochi danni se esce. Non e'
    una scusa per lasciarla in giro: l'interfaccia non la mostra mai per intero
    e non la rimanda mai al browser.

    `scope_json` e' la risposta di GET /keys/current al momento del
    salvataggio, tenuta per poter spiegare *perche'* una chiave e' stata
    accettata o rifiutata, anche mesi dopo.
    """

    __tablename__ = "zotero_credentials"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    key = Column(String, nullable=False)
    zotero_user_id = Column(String, nullable=True)
    zotero_username = Column(String, nullable=True)
    scope_json = Column(Text, nullable=True)
    verdict = Column(String, nullable=False)  # ok | stretta | larga
    checked_at = Column(DateTime, default=utcnow)

    user = relationship("User")

    @property
    def masked(self) -> str:
        return "…" + self.key[-4:] if self.key else ""


class Binding(Base):
    """Un paper = una collezione. Due gambe: si legge da una, si scrive sull'altra."""

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
    """Registro delle scritture verso Zotero, e chiave di idempotenza (§9.1).

    Il vincolo di unicita' e' la protezione vera contro i doppioni da retry:
    pyzotero manda un Zotero-Write-Token nuovo a ogni invocazione, quindi
    protegge i propri tentativi interni ma non una seconda chiamata del
    chiamante. Questa riga si', perche' vive al livello dove il retry accade.
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


def init_db() -> None:
    """Migrazioni additive, come nel resto degli strumenti di casa: girano da
    sole all'avvio, non rinominano e non droppano niente."""
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    Base.metadata.create_all(engine)
