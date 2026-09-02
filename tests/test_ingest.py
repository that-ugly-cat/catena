"""
Ingest — SPEC §5, §4.3, §6 and §9.1.

The translation server is not exercised here: it runs as a container beside
catena and there is none on this machine. What is exercised is everything
around it — the ladder that decides which rung an identifier is on, the
deduplication that stops a second copy of a paper, the author cross-check that
catches a mistyped DOI, and the refusals.

That split is deliberate rather than convenient. The client is one function that
posts a string and reads JSON; the judgement is all here, and judgement is what
is worth pinning down.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from catena.server import ingest as ing
from catena.server.translation import Resolution, TranslationError, classify, normalise

ROSATO = {
    "itemType": "journalArticle",
    "title": "Community participation: lessons for maternal, newborn, and child health",
    "creators": [{"creatorType": "author", "lastName": "Rosato", "firstName": "M"}],
    "date": "2008",
    "DOI": "10.1016/S0140-6736(08)61406-3",
    "publicationTitle": "The Lancet",
}
REPORT = {
    "itemType": "webpage",
    "title": "Global report on infodemic management",
    "creators": [{"creatorType": "author", "name": "World Health Organization"}],
    "date": "2022",
    "url": "https://www.who.int/publications/x",
}


class StubTranslation:
    """Stands in for the container: a dict of identifier -> item."""

    def __init__(self, table: dict[str, dict]):
        self.table = table
        self.calls: list[str] = []

    def resolve(self, identifier: str) -> Resolution:
        self.calls.append(identifier)
        kind = classify(identifier)
        if not kind:
            raise TranslationError(f"{identifier!r} is not an identifier catena resolves")
        value = normalise(identifier, kind)
        item = self.table.get(value)
        if not item:
            raise TranslationError(f"nothing resolved {value}")
        return Resolution(identifier=value, kind=kind, items=[item])


@dataclass
class FakeBinding:
    id: int = 1
    source_library: str = "groups/1"
    source_collection_key: str = "AAA"
    deposit_library: str = "groups/2"
    deposit_collection_key: str = "BBB"

    @property
    def one_legged(self) -> bool:
        return self.source_library == self.deposit_library


def item(key, title, doi=None, year="2008", library="groups/1"):
    return {
        "key": key, "library": library, "title": title, "doi": doi,
        "year": year, "first_author": "X", "csljson": {},
    }


# --- the ladder ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,kind",
    [
        ("10.1016/S0140-6736(08)61406-3", "doi"),
        ("https://doi.org/10.1234/abc", "doi"),
        ("arXiv:2401.01234", "arxiv"),
        ("9780199212094", "isbn"),
        ("978-0-19-921209-4", "isbn"),
        ("https://www.who.int/publications/x", "url"),
        ("30798313", "pmid"),
        ("Rosato et al. 2008", None),
        ("", None),
    ],
)
def test_the_identifier_ladder(raw, kind):
    assert classify(raw) == kind


def test_a_doi_written_as_a_url_is_still_a_doi():
    assert normalise("https://doi.org/10.1234/abc", "doi") == "10.1234/abc"


def test_a_title_is_not_an_identifier():
    """SPEC §5: resolving by title is how a plausible wrong reference is made."""
    tr = StubTranslation({})
    rows = ing.build_plan(None, tr, FakeBinding(), ["Rosato et al. 2008"], existing=[])
    assert rows[0].outcome == "unresolved"
    assert "not an identifier" in rows[0].warnings[0]


# --- deduplication ------------------------------------------------------------


def test_an_exact_doi_match_is_already_present():
    tr = StubTranslation({"10.1016/S0140-6736(08)61406-3": ROSATO})
    pool = [item("EB734IKH", "Community participation", doi="10.1016/S0140-6736(08)61406-3")]
    rows = ing.build_plan(None, tr, FakeBinding(), ["10.1016/S0140-6736(08)61406-3"], existing=pool)
    assert rows[0].outcome == "present"
    assert rows[0].existing["key"] == "EB734IKH"


def test_the_doi_match_ignores_case():
    tr = StubTranslation({"10.1016/s0140-6736(08)61406-3": dict(ROSATO, DOI="10.1016/S0140-6736(08)61406-3")})
    pool = [item("K1", "x", doi="10.1016/s0140-6736(08)61406-3")]
    rows = ing.build_plan(None, tr, FakeBinding(), ["10.1016/s0140-6736(08)61406-3"], existing=pool)
    assert rows[0].outcome == "present"


def test_same_title_without_a_shared_identifier_is_not_decided():
    """The fuzzy rung asks; it does not choose (SPEC §5.1)."""
    tr = StubTranslation({"10.9999/new": dict(ROSATO, DOI="10.9999/new")})
    pool = [item("OLD", ROSATO["title"], doi=None)]
    rows = ing.build_plan(None, tr, FakeBinding(), ["10.9999/new"], existing=pool)
    assert rows[0].outcome == "ambiguous"
    assert rows[0].candidates[0]["key"] == "OLD"
    assert "does not decide" in rows[0].warnings[-1]


def test_titles_compare_without_accents_or_punctuation():
    """Surnames are not ASCII (SPEC §14.3, trap 7)."""
    tr = StubTranslation(
        {"10.1234/x": dict(ROSATO, DOI="10.1234/x", title="Grundström, health: a review")}
    )
    pool = [item("OLD", "Grundstrom health a review")]
    rows = ing.build_plan(None, tr, FakeBinding(), ["10.1234/x"], existing=pool)
    assert rows[0].outcome == "ambiguous"


def test_a_registrant_code_shorter_than_four_digits_is_not_a_doi():
    """`10.1/x` looks like one and is not; the shape is part of the check."""
    assert classify("10.1/x") is None
    assert classify("10.1234/x") == "doi"


def test_something_genuinely_new_is_new():
    tr = StubTranslation({"10.1016/S0140-6736(08)61406-3": ROSATO})
    rows = ing.build_plan(None, tr, FakeBinding(), ["10.1016/S0140-6736(08)61406-3"], existing=[])
    assert rows[0].outcome == "new"
    assert rows[0].raw == ROSATO, "the translator's item is carried into the plan"


# --- the guards ---------------------------------------------------------------


def test_a_surname_that_disagrees_with_the_doi_is_flagged():
    """SPEC §5.0: in `[Assan, 10.1136/…]` the name is a check, not a handle."""
    tr = StubTranslation({"10.1016/S0140-6736(08)61406-3": ROSATO})
    rows = ing.build_plan(
        None, tr, FakeBinding(), ["10.1016/S0140-6736(08)61406-3"],
        {"10.1016/S0140-6736(08)61406-3": "Assan"}, existing=[],
    )
    assert any("copied wrong" in w for w in rows[0].warnings)


def test_a_surname_that_agrees_is_quiet():
    tr = StubTranslation({"10.1016/S0140-6736(08)61406-3": ROSATO})
    rows = ing.build_plan(
        None, tr, FakeBinding(), ["10.1016/S0140-6736(08)61406-3"],
        {"10.1016/S0140-6736(08)61406-3": "Rosato"}, existing=[],
    )
    assert not any("copied wrong" in w for w in rows[0].warnings)


def test_an_uncertain_item_type_is_put_in_front_of_a_person():
    """CSL formats by type: a report filed as a webpage is wrong in every style."""
    tr = StubTranslation({"https://www.who.int/publications/x": REPORT})
    rows = ing.build_plan(None, tr, FakeBinding(), ["https://www.who.int/publications/x"], existing=[])
    assert rows[0].item_type == "webpage"
    assert any("webpage" in w for w in rows[0].warnings)


# --- provenance ---------------------------------------------------------------


def test_the_tag_carries_the_bearing_and_not_just_the_source():
    assert ing.BEARING_TAGS["contradicts"] == "catena:contradicts"
    assert ing.BEARING_TAGS["supports_directly"] == "catena:supports"


def test_the_note_holds_the_quote_and_the_trace():
    note = ing.provenance_note(
        "hasstZLyOQuT", "supports",
        [{"quote": "Participation improved outcomes.", "location": "Results",
          "bearing": "supports_directly", "why": "direct"}],
    )
    assert "Participation improved outcomes." in note
    assert "hasstZLyOQuT" in note
    assert "contrarian.borant.eu/runs/hasstZLyOQuT" in note
    assert "Results" in note


def test_the_item_sent_to_zotero_drops_the_translator_bookkeeping():
    row = ing.Row(identifier="10.1/x", raw=dict(ROSATO, key="ZZZ", version=7,
                                                dateAdded="2020", collections=["OTHER"]))
    sent = ing._to_zotero_item(row, "BBB", {"verdict": "contradicts"})
    assert "key" not in sent and "version" not in sent and "dateAdded" not in sent
    assert sent["collections"] == ["BBB"]
    assert {"tag": "catena:contradicts"} in sent["tags"]
    assert sent["title"] == ROSATO["title"]


def test_a_plan_row_without_translator_output_refuses_to_be_written():
    with pytest.raises(RuntimeError, match="rebuild it"):
        ing._to_zotero_item(ing.Row(identifier="10.1/x"), "BBB")
