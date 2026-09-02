"""
Audit di un manoscritto — SPEC §13.3.

Legge un .docx con campi Zotero e dice cosa non va. Non scrive niente, da
nessuna parte, e non ha bisogno di rete ne' di credenziali: tutto quello che
controlla e' deducibile dal file. E' il primo pezzo di `catena` per questo
motivo — vale gia' da solo, gira su documenti scritti da altri, e serve a
validare `catena` stessa quando ci sara' il resto.

Il controllo che conta di piu' e' il surrogato duplicato. Quando un URI non
risolve, Zotero costruisce un item surrogato distinto (verificato in Word, SPEC
§12.2 caso 5): lo stesso paper raggiunto da due URI diversi prende due numeri e
due voci di bibliografia. In Vancouver e' un `(4)` di troppo che nessuno nota;
in APA diventa un `2008a`/`2008b` che sembra un secondo lavoro dello stesso
autore.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .fields import (
    Citation,
    has_bibliography,
    looks_like_doi,
    parse_citations,
    parse_prefs,
)
from .ooxml import Document

# Gravita': errore = la bibliografia risultante e' sbagliata;
# avviso = fragile o non portabile; nota = da sapere, non da correggere.
ERRORE, AVVISO, NOTA = "errore", "avviso", "nota"

ORDER = {ERRORE: 0, AVVISO: 1, NOTA: 2}


@dataclass
class Finding:
    level: str
    code: str
    message: str
    detail: str = ""

    def __str__(self) -> str:
        s = f"[{self.level}] {self.code}: {self.message}"
        return s + (f"\n    {self.detail}" if self.detail else "")


@dataclass
class Report:
    path: Path
    counts: dict[str, int]
    findings: list[Finding]
    style: str | None
    citations: list[Citation]

    @property
    def clean(self) -> bool:
        return not any(f.level == ERRORE for f in self.findings)

    def by_level(self, level: str) -> list[Finding]:
        return [f for f in self.findings if f.level == level]


def audit(path: str | Path) -> Report:
    doc = Document.open(path)
    cits = parse_citations(doc)
    prefs = parse_prefs(doc)
    found: list[Finding] = []

    if not cits:
        found.append(
            Finding(
                NOTA,
                "nessun-campo",
                "il documento non contiene campi Zotero",
                "tutte le citazioni, se ci sono, sono testo battuto a mano: "
                "e' il caso della SPEC §13.1, non un difetto",
            )
        )

    _check_surrogate_duplicates(cits, found)
    _check_uris(cits, found)
    _check_metadata(cits, found)
    _check_citation_ids(cits, found)
    _check_note_styles(cits, found)
    _check_document_level(doc, cits, prefs, found)

    found.sort(key=lambda f: (ORDER[f.level], f.code))
    return Report(
        path=Path(path),
        counts=doc.counts(),
        findings=found,
        style=prefs.style_name if prefs else None,
        citations=cits,
    )


# --- controlli ---------------------------------------------------------------


def _check_surrogate_duplicates(cits: list[Citation], out: list[Finding]) -> None:
    """Stesso paper sotto URI diversi: due voci in bibliografia (SPEC §3.2)."""
    by_sig: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, str] = {}
    for c in cits:
        for it in c.items:
            sig = it.signature()
            by_sig[sig].add("|".join(sorted(it.uris)) or "<senza uri>")
            labels.setdefault(
                sig, f"{it.first_author or '?'} {it.year or '?'} — {it.title[:60]}"
            )
    for sig, uris in by_sig.items():
        if len(uris) > 1:
            out.append(
                Finding(
                    ERRORE,
                    "surrogato-duplicato",
                    f"lo stesso paper compare sotto {len(uris)} URI diversi: {labels[sig]}",
                    "produce due voci di bibliografia e due numeri; in APA diventa "
                    "una disambiguazione per anno (2008a/2008b) che non esiste.\n    "
                    + "\n    ".join(sorted(uris)),
                )
            )


def _check_uris(cits: list[Citation], out: list[Finding]) -> None:
    local: list[str] = []
    missing: list[str] = []
    for c in cits:
        for it in c.items:
            if not it.uris:
                missing.append(f"citazione #{c.order + 1}")
            elif it.is_local_uri:
                local.append(it.library or "?")
    if missing:
        out.append(
            Finding(
                ERRORE,
                "uri-assente",
                f"{len(missing)} item senza URI",
                "Zotero risolve per URI; senza, ricade sull'id numerico locale e "
                "puo' riagganciare l'item sbagliato (SPEC §7.7)",
            )
        )
    if local:
        profiles = sorted(set(local))
        out.append(
            Finding(
                AVVISO,
                "uri-locale",
                f"{len(local)} item puntano a un profilo Zotero locale",
                "questi URI risolvono su una sola macchina al mondo: per i coautori "
                "gli item sono orfani e si formattano solo dai dati incorporati "
                f"(SPEC §7.2). Profili: {', '.join(profiles)}",
            )
        )


def _check_metadata(cits: list[Citation], out: list[Finding]) -> None:
    bad_doi: list[str] = []
    no_title: list[str] = []
    no_data: list[str] = []
    for c in cits:
        for it in c.items:
            if not it.item_data:
                no_data.append(f"#{c.order + 1}")
                continue
            raw = it.item_data.get("DOI")
            if raw and not looks_like_doi(raw):
                bad_doi.append(f"{it.first_author or '?'} {it.year or '?'}: DOI={raw!r}")
            if not it.title:
                no_title.append(f"#{c.order + 1}")
    if no_data:
        out.append(
            Finding(
                ERRORE,
                "itemdata-assente",
                f"{len(no_data)} item senza dati incorporati",
                "se l'URI non risolve non c'e' nulla su cui ricadere: Zotero apre "
                "un dialogo di riselezione o solleva un errore",
            )
        )
    if bad_doi:
        out.append(
            Finding(
                AVVISO,
                "doi-malformato",
                f"{len(bad_doi)} item con un DOI che non e' un DOI",
                "\n    ".join(bad_doi[:10]),
            )
        )
    if no_title:
        out.append(
            Finding(AVVISO, "titolo-assente", f"{len(no_title)} item senza titolo")
        )


def _check_citation_ids(cits: list[Citation], out: list[Finding]) -> None:
    ids = [c.citation_id for c in cits if c.citation_id]
    dupes = [k for k, v in Counter(ids).items() if v > 1]
    if dupes:
        out.append(
            Finding(
                AVVISO,
                "citationid-duplicato",
                f"{len(dupes)} citationID compaiono piu' di una volta",
                "devono essere unici per occorrenza, non per item (SPEC §7.1): "
                f"{', '.join(dupes[:8])}",
            )
        )


def _check_note_styles(cits: list[Citation], out: list[Finding]) -> None:
    notes = [c for c in cits if c.is_footnote_style]
    if notes:
        out.append(
            Finding(
                NOTA,
                "stile-con-note",
                f"{len(notes)} citazioni hanno noteIndex diverso da zero",
                "il documento usa uno stile con note a pie' di pagina: `catena` "
                "non lo gestisce ancora e lo rifiuta esplicitamente (SPEC §7.6)",
            )
        )


def _check_document_level(doc, cits, prefs, out: list[Finding]) -> None:
    if cits and not prefs:
        out.append(
            Finding(
                ERRORE,
                "prefs-assenti",
                "ci sono campi Zotero ma nessuna proprieta' ZOTERO_PREF",
                "senza le preferenze di documento Zotero non sa con che stile "
                "formattare, e il cambio di stile da Word non funziona (SPEC §7.4)",
            )
        )
    if prefs and prefs.field_type and prefs.field_type != "Field":
        out.append(
            Finding(
                AVVISO,
                "fieldtype",
                f"fieldType = {prefs.field_type!r}, atteso 'Field'",
                "l'iniettore lavora solo su campi Word veri",
            )
        )
    if cits and not has_bibliography(doc):
        out.append(
            Finding(
                AVVISO,
                "bibliografia-assente",
                "ci sono citazioni ma nessun campo bibliografia",
                "voluto in un abstract o in una lettera; sospetto in un manoscritto",
            )
        )
    authors = doc.revision_authors
    if authors:
        out.append(
            Finding(
                NOTA,
                "revisioni-attive",
                f"il documento ha revisioni tracciate di {len(authors)} autori",
                "un'iniezione deve conservare la marcatura e la sua attribuzione "
                f"(SPEC §8.3): {', '.join(authors)}",
            )
        )


# --- resa a schermo ----------------------------------------------------------


def render(report: Report) -> str:
    c = report.counts
    lines = [
        f"{report.path.name}",
        "",
        f"  paragrafi {c['paragrafi']}   caratteri {c['caratteri']}   "
        f"campi Zotero {c['campi_zotero']}   bibliografia {c['campi_bibliografia']}",
        f"  revisioni +{c['revisioni_inserite']} -{c['revisioni_cancellate']}   "
        f"commenti {c['commenti']}   note {c['note_a_pie']}",
    ]
    if report.style:
        lines.append(f"  stile {report.style}")
    lines.append("")

    if not report.findings:
        lines.append("  niente da segnalare.")
        return "\n".join(lines)

    for level in (ERRORE, AVVISO, NOTA):
        for f in report.by_level(level):
            lines.append("  " + str(f).replace("\n", "\n  "))
            lines.append("")

    n_err = len(report.by_level(ERRORE))
    n_warn = len(report.by_level(AVVISO))
    lines.append(f"  {n_err} errori, {n_warn} avvisi, {len(report.by_level(NOTA))} note")
    return "\n".join(lines)
