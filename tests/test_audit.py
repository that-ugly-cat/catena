"""
Test dell'audit contro il fixture dello spike.

Il fixture non e' un file inventato per i test: e' lo stesso `.docx` generato a
mano, aperto in Word con Zotero e passato al Refresh (SPEC §12.2). Sappiamo cosa
Word ne fa. Quindi le asserzioni qui sotto non verificano che il codice sia
coerente con se stesso — verificano che riproduca staticamente un
comportamento osservato.

    uv run pytest          (oppure: python -m pytest)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catena.audit import ERRORE, audit, render  # noqa: E402
from catena.fields import parse_citations, parse_prefs  # noqa: E402
from catena.ooxml import Document, xml_text  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "spike" / "catena-spike.docx"


@pytest.fixture(scope="module")
def doc() -> Document:
    return Document.open(FIXTURE)


# --- il parser OOXML ---------------------------------------------------------


def test_w_t_non_intercetta_i_tag_omonimi():
    """SPEC §14.3, trappola 8: `<w:t[^>]*>` prende anche `<w:tcPr>`."""
    frag = "<w:tcPr><w:vAlign w:val='top'/></w:tcPr><w:t>vero testo</w:t><w:tab/>"
    assert xml_text(frag) == "vero testo"


def test_entita_xml_risolte():
    assert xml_text("<w:t>Bullo &amp; Hearn</w:t>") == "Bullo & Hearn"


# --- struttura del fixture ---------------------------------------------------


def test_il_fixture_ha_i_cinque_casi(doc: Document):
    cits = parse_citations(doc)
    assert len(cits) == 5
    assert doc.counts()["campi_bibliografia"] == 1


def test_citation_id_unici_per_occorrenza(doc: Document):
    """SPEC §7.1: unici per occorrenza, non per item."""
    ids = [c.citation_id for c in parse_citations(doc)]
    assert len(set(ids)) == len(ids) == 5


def test_caso_2_non_ha_il_campo_id(doc: Document):
    """SPEC §7.7: con `uris` presenti l'id non serve, e Word lo conferma."""
    assert parse_citations(doc)[1].items[0].raw_id is None


def test_caso_4_e_una_citazione_raggruppata(doc: Document):
    """SPEC §11.2 punto 12: forma (1,2), un campo con due citationItems."""
    assert len(parse_citations(doc)[3].items) == 2


def test_uris_sempre_presenti(doc: Document):
    """SPEC §7.7: il ramo di fallback itera `uris`, che non puo' mancare."""
    for c in parse_citations(doc):
        for item in c.items:
            assert item.uris, f"citazione #{c.order + 1} senza uris"


def test_prefs_ricomposte(doc: Document):
    prefs = parse_prefs(doc)
    assert prefs is not None
    assert prefs.style_name == "vancouver"
    assert prefs.field_type == "Field"
    assert prefs.chunks == 2, "il taglio a 255 caratteri deve produrre due chunk"


# --- i controlli -------------------------------------------------------------


def test_audit_trova_il_surrogato_duplicato():
    """Il difetto osservato in Word (SPEC §12.2 caso 5), ritrovato offline."""
    report = audit(FIXTURE)
    codici = [f.code for f in report.by_level(ERRORE)]
    assert "surrogato-duplicato" in codici
    assert not report.clean


def test_audit_trova_il_doi_malformato():
    report = audit(FIXTURE)
    assert any(f.code == "doi-malformato" for f in report.findings)


def test_render_non_esplode():
    assert FIXTURE.name in render(audit(FIXTURE))


# --- la CLI ------------------------------------------------------------------


def test_cli_esce_con_1_se_ci_sono_errori():
    r = subprocess.run(
        [sys.executable, "-m", "catena.cli", "audit", str(FIXTURE)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        env={**__import__("os").environ, "PYTHONPATH": "src"},
    )
    assert r.returncode == 1
    assert "surrogato-duplicato" in r.stdout


def test_cli_segnala_un_file_che_non_e_un_docx(tmp_path: Path):
    finto = tmp_path / "finto.docx"
    finto.write_bytes(b"non sono uno zip")
    with pytest.raises(Exception):
        audit(finto)
