"""
Sources without a DOI: caller metadata, PDFs, ISBNs, and the URL rung.

The cases are the ones a book bibliography actually holds — a newspaper page the
translator files as `webpage`, a regulator's report that exists only as a PDF, a
law, a Nobel lecture, an Italian edition whose ISBN no catalogue knows, a
publisher's page given for a chapter. Zotero's item-type schema is stubbed with
the fields Zotero really has for those types; the translator is stubbed as in
test_ingest.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from catena.server import ingest as ing
from catena.server import records as rec
from catena.server.translation import TranslationError

from test_ingest import FakeBinding, StubTranslation, item

TODAY = dt.date(2026, 10, 16)

FIELDS = {
    "webpage": ["title", "abstractNote", "websiteTitle", "websiteType", "date",
                "shortTitle", "url", "accessDate", "language", "rights", "extra"],
    "newspaperArticle": ["title", "abstractNote", "publicationTitle", "place", "edition",
                         "date", "section", "pages", "language", "shortTitle", "ISSN",
                         "url", "accessDate", "extra"],
    "report": ["title", "abstractNote", "reportNumber", "reportType", "seriesTitle",
               "place", "institution", "date", "pages", "language", "shortTitle",
               "url", "accessDate", "extra"],
    "book": ["title", "abstractNote", "series", "seriesNumber", "volume", "edition",
             "place", "publisher", "date", "numPages", "language", "ISBN",
             "shortTitle", "url", "accessDate", "extra"],
    "bookSection": ["title", "abstractNote", "bookTitle", "series", "edition", "place",
                    "publisher", "date", "pages", "language", "ISBN", "shortTitle",
                    "url", "accessDate", "extra"],
    "statute": ["nameOfAct", "abstractNote", "code", "codeNumber", "publicLawNumber",
                "dateEnacted", "pages", "section", "session", "history", "language",
                "shortTitle", "url", "accessDate", "extra"],
    "presentation": ["title", "abstractNote", "presentationType", "date", "place",
                     "meetingName", "url", "accessDate", "language", "extra"],
    "journalArticle": ["title", "abstractNote", "publicationTitle", "volume", "issue",
                       "pages", "date", "DOI", "ISSN", "url", "accessDate", "extra"],
}
CREATORS = {
    "webpage": ["author", "contributor", "translator"],
    "newspaperArticle": ["author", "contributor", "reviewedAuthor", "translator"],
    "report": ["author", "contributor", "translator", "seriesEditor"],
    "book": ["author", "contributor", "editor", "translator", "seriesEditor"],
    "bookSection": ["author", "contributor", "editor", "bookAuthor", "translator"],
    "statute": ["author", "contributor"],
    "presentation": ["presenter", "contributor"],
    "journalArticle": ["author", "contributor", "editor", "reviewedAuthor", "translator"],
}


class StubSchema:
    def fields(self, item_type):
        if item_type not in FIELDS:
            raise KeyError(item_type)
        return FIELDS[item_type]

    def creator_types(self, item_type):
        if item_type not in CREATORS:
            raise KeyError(item_type)
        return CREATORS[item_type]


class FailingTranslation(StubTranslation):
    """A translator that answers like the real one when it cannot read a source."""

    def resolve(self, identifier):
        self.calls.append(identifier)
        try:
            return super().resolve(identifier)
        except TranslationError:
            raise TranslationError(f"nothing resolved {identifier}") from None


def plan(tr, entries, pool=None, identifiers=None, hints=None):
    return ing.build_plan(None, tr, FakeBinding(), identifiers or [], hints,
                          entries=entries, existing=pool or [], schema=StubSchema(),
                          today=TODAY)


REGISTER_URL = "https://www.theregister.com/2006/06/26/drug_trial_fingers/"
REGISTER = {
    "itemType": "webpage",
    "title": "Drug trial 'Elephant Man' to lose fingers and toes",
    "creators": [{"creatorType": "author", "lastName": "Haines", "firstName": "Lester"}],
    "websiteTitle": "The Register",
    "websiteType": "news",
    "date": "2006-06-26",
    "url": REGISTER_URL,
    "accessDate": "2026-10-10T08:00:00Z",
}
ANSM_PDF = ("https://archive.ansm.sante.fr/var/ansm_site/storage/original/application/"
            "744c7c6daf96b141bc9509e2f85c227e.pdf")
ANSM_RECORD = {
    "title": "Report on the causes of the accident during a Phase 1 clinical trial "
             "in Rennes in January 2016",
    "authors": ["Temporary Specialist Scientific Committee (TSSC) «FAAH»"],
    "publisher": "ANSM",
    "date": "2016",
}


# --- the caller's type and fields win, and show ---------------------------------


def test_a_type_override_carries_the_container_across():
    """The Register filed as a webpage becomes a newspaper article, masthead kept."""
    rows = plan(StubTranslation({REGISTER_URL: REGISTER}),
                [{"id": REGISTER_URL, "item_type": "newspaperArticle", "ref_key": "15-04"}])
    r = rows[0]
    assert r.outcome == "new"
    assert r.item_type == "newspaperArticle"
    assert r.raw["publicationTitle"] == "The Register"
    assert "websiteTitle" not in r.raw
    assert r.source == "translator+caller"
    assert r.ref_key == "15-04"
    assert not any("translator called this" in w for w in r.warnings), \
        "the type was set on purpose; warning about it is noise"


def test_a_field_with_no_room_in_the_new_type_is_listed_not_lost_silently():
    rows = plan(StubTranslation({REGISTER_URL: REGISTER}),
                [{"id": REGISTER_URL, "item_type": "newspaperArticle"}])
    assert rows[0].dropped == {"websiteType": "news"}
    assert any("nowhere to go" in w for w in rows[0].warnings)


def test_every_caller_field_is_shown_against_the_translators():
    rows = plan(StubTranslation({REGISTER_URL: REGISTER}), [{
        "id": REGISTER_URL, "item_type": "newspaperArticle",
        "metadata": {"date": "2006-06-27", "place": "London"},
    }])
    r = rows[0]
    assert {"field": "date", "translator": "2006-06-26", "caller": "2006-06-27"} in r.overridden
    assert r.asserted == {"place": "London"}
    assert r.raw["date"] == "2006-06-27"


def test_authors_replace_the_translators_and_read_people_and_institutions():
    rows = plan(StubTranslation({REGISTER_URL: REGISTER}), [{
        "id": REGISTER_URL,
        "metadata": {"authors": ["Haines, L.", "The Register staff"]},
    }])
    creators = rows[0].raw["creators"]
    assert creators[0] == {"creatorType": "author", "lastName": "Haines", "firstName": "L."}
    assert creators[1] == {"creatorType": "author", "name": "The Register staff"}
    assert rows[0].overridden[0]["field"] == "authors"


def test_a_caller_title_that_disagrees_points_at_the_wrong_item():
    """15-17: the URL is the Routledge page of the book, the source is a chapter."""
    url = "https://www.routledge.com/Vivisection-in-Historical-Perspective/Rupke/p/book/9781041163015"
    book = {"itemType": "book", "title": "Vivisection in Historical Perspective",
            "creators": [{"creatorType": "editor", "lastName": "Rupke", "firstName": "N"}],
            "publisher": "Routledge", "url": url}
    rows = plan(StubTranslation({url: book}), [{
        "id": url, "item_type": "bookSection",
        "metadata": {"title": "Vivisection and the emergence of experimental physiology "
                              "in nineteenth-century France",
                     "authors": ["Elliott, P."], "container": "Vivisection in Historical "
                     "Perspective", "date": "1987"},
    }])
    r = rows[0]
    assert r.outcome == "new"
    assert r.raw["bookTitle"] == "Vivisection in Historical Perspective"
    assert any("disagree" in w for w in r.warnings)
    # the editor survives the type change, the caller's author is in front
    assert [c["creatorType"] for c in r.raw["creators"]] == ["author", "editor"]


def test_a_statute_gets_its_own_field_names():
    url = "https://www.legislation.gov.uk/ukpga/Vict/39-40/77/contents/enacted"
    rows = plan(FailingTranslation({}), [{
        "id": url, "item_type": "statute",
        "metadata": {"title": "Cruelty to Animals Act 1876", "date": "1876-08-15",
                     "number": "1876 c. 77"},
    }])
    r = rows[0]
    assert r.outcome == "new"
    assert r.raw["nameOfAct"] == "Cruelty to Animals Act 1876"
    assert r.raw["dateEnacted"] == "1876-08-15"
    assert r.raw["publicLawNumber"] == "1876 c. 77"
    assert r.raw["url"] == url
    assert r.metadata["title"] == "Cruelty to Animals Act 1876", "brief reads the family"
    assert r.metadata["year"] == "1876"


def test_a_lecture_is_given_by_a_presenter():
    url = "https://www.nobelprize.org/prizes/peace/1952/schweitzer/lecture/"
    rows = plan(FailingTranslation({}), [{
        "id": url, "item_type": "presentation",
        "metadata": {"title": "The Problem of Peace", "authors": ["Schweitzer, Albert"],
                     "date": "1954-11-04", "kind": "Nobel Lecture", "place": "Oslo"},
    }])
    r = rows[0]
    assert r.raw["creators"][0]["creatorType"] == "presenter"
    assert r.raw["presentationType"] == "Nobel Lecture"


# --- refusals, at plan time and not at apply time ----------------------------------


def test_a_field_the_type_does_not_have_is_refused_in_the_plan():
    rows = plan(FailingTranslation({}), [{
        "item_type": "webpage",
        "metadata": {"title": "x", "fields": {"publicationTitle": "y"}},
    }])
    assert rows[0].outcome == "invalid"
    assert "not a field of webpage" in rows[0].warnings[-1]
    assert "websiteTitle" in rows[0].warnings[-1], "the valid names are offered"


def test_an_unknown_metadata_key_is_refused():
    rows = plan(FailingTranslation({}), [{"item_type": "book", "metadata": {"title": "x",
                                                                             "jornal": "y"}}])
    assert rows[0].outcome == "invalid"
    assert "jornal" in rows[0].warnings[-1]


def test_an_unknown_item_type_is_refused():
    rows = plan(FailingTranslation({}), [{"item_type": "newspaper", "metadata": {"title": "x"}}])
    assert rows[0].outcome == "invalid"
    assert "not a Zotero item type" in rows[0].warnings[-1]


def test_a_record_without_a_type_is_refused_when_nothing_resolved():
    rows = plan(FailingTranslation({}), [{"metadata": {"title": "x"}}])
    assert rows[0].outcome == "invalid"
    assert "item_type is required" in rows[0].warnings[-1]


def test_an_entry_with_unknown_keys_is_refused():
    rows = plan(FailingTranslation({}), [{"doi": "10.1234/x"}])
    assert rows[0].outcome == "invalid"


def test_a_doi_that_does_not_resolve_is_not_rescued_by_metadata():
    """A wrong DOI dressed in right metadata is the invisible error in its purest form."""
    rows = plan(FailingTranslation({}), [{
        "id": "10.9999/does-not-exist", "item_type": "journalArticle",
        "metadata": {"title": "Something", "date": "2020"},
    }])
    assert rows[0].outcome == "unresolved"
    assert any("copied wrong" in w for w in rows[0].warnings)


def test_a_title_as_id_is_refused_even_with_metadata():
    rows = plan(FailingTranslation({}), [{
        "id": "Rosato et al. 2008", "item_type": "book", "metadata": {"title": "x"},
    }])
    assert rows[0].outcome == "invalid"


# --- no identifier, and PDFs ---------------------------------------------------------


def test_a_pdf_without_metadata_says_what_to_do():
    rows = plan(FailingTranslation({}), [{"id": ANSM_PDF}])
    assert rows[0].outcome == "unresolved"
    assert any("PDF" in w for w in rows[0].warnings)


def test_a_pdf_with_metadata_keeps_its_url_and_gets_an_access_date():
    rows = plan(FailingTranslation({}), [{"id": ANSM_PDF, "item_type": "report",
                                          "metadata": ANSM_RECORD}])
    r = rows[0]
    assert r.outcome == "new"
    assert r.source == "caller"
    assert r.identifier == ANSM_PDF and r.kind == "url"
    assert r.raw["url"] == ANSM_PDF
    assert r.raw["institution"] == "ANSM"
    assert r.raw["creators"] == [{"creatorType": "author",
                                  "name": "Temporary Specialist Scientific Committee (TSSC) «FAAH»"}]
    assert r.defaulted == {"url": ANSM_PDF, "accessDate": "2026-10-16"}
    assert any("nothing checked it" in w for w in r.warnings)


def test_a_given_access_date_is_not_overwritten():
    rows = plan(FailingTranslation({}), [{"id": ANSM_PDF, "item_type": "report",
                                          "metadata": dict(ANSM_RECORD, accessed="2026-09-01")}])
    assert rows[0].raw["accessDate"] == "2026-09-01"
    assert "accessDate" not in rows[0].defaulted


def test_a_translator_only_item_gets_no_invented_access_date():
    rows = plan(StubTranslation({"https://www.who.int/x": {
        "itemType": "report", "title": "T", "url": "https://www.who.int/x"}}),
        [{"id": "https://www.who.int/x"}])
    assert "accessDate" not in rows[0].raw


def test_a_source_with_no_identifier_gets_a_stable_one():
    """The idempotency row needs a key, and a retry must find the same key."""
    entry = {"item_type": "report", "metadata": ANSM_RECORD}
    a = plan(FailingTranslation({}), [entry])[0]
    b = plan(FailingTranslation({}), [dict(entry, metadata=dict(ANSM_RECORD))])[0]
    assert a.identifier.startswith("ref:") and a.kind == "manual"
    assert a.identifier == b.identifier
    other = plan(FailingTranslation({}), [{"item_type": "report",
                                           "metadata": dict(ANSM_RECORD, date="2017")}])[0]
    assert other.identifier != a.identifier


# --- ISBN ----------------------------------------------------------------------------


def test_isbn_ten_and_thirteen_are_the_same_book():
    assert rec.isbn13("8856501805") == "9788856501803"
    assert rec.isbns("9788856501803 8856501805") == {"9788856501803"}


def test_an_isbn_no_catalogue_knows_becomes_a_book_from_metadata():
    rows = plan(FailingTranslation({}), [{
        "id": "978-88-565-0180-3", "item_type": "book",
        "metadata": {"title": "Liberazione animale", "authors": ["Singer, Peter"],
                     "publisher": "Il Saggiatore", "date": "2010"},
    }])
    r = rows[0]
    assert r.outcome == "new"
    assert r.identifier == "9788856501803" and r.kind == "isbn"
    assert r.raw["ISBN"] == "9788856501803"


def test_an_unknown_isbn_without_metadata_says_what_to_do():
    rows = plan(FailingTranslation({}), [{"id": "978-88-565-0180-3"}])
    assert rows[0].outcome == "unresolved"
    assert any("ISBN" in w for w in rows[0].warnings)


def test_the_isbn_match_reads_isbn_ten_in_the_library():
    book = {"itemType": "book", "title": "Liberazione animale", "ISBN": "9788856501803"}
    pool = [dict(item("OLD", "Liberazione animale"), isbn="88-565-0180-5 9999999999999")]
    rows = plan(StubTranslation({"9788856501803": book}), [{"id": "9788856501803"}], pool)
    assert rows[0].outcome == "present"
    assert rows[0].existing == {"key": "OLD", "library": "groups/1", "match": "isbn"}


def test_two_chapters_of_one_book_share_an_isbn_and_are_not_duplicates():
    pool = [dict(item("CH1", "First chapter"), isbn="9780199212094")]
    rows = plan(FailingTranslation({}), [{
        "item_type": "bookSection",
        "metadata": {"title": "A quite different second chapter", "isbn": "9780199212094",
                     "container": "The Volume"},
    }], pool)
    assert rows[0].outcome == "new"


# --- the URL rung ----------------------------------------------------------------------


@pytest.mark.parametrize("a,b", [
    ("http://www.example.org/a/b/", "https://example.org/a/b"),
    ("https://example.org/a?utm_source=x&id=3#top", "https://example.org/a?id=3"),
    ("https://EXAMPLE.org/a?b=2&a=1", "https://example.org/a?a=1&b=2"),
])
def test_urls_that_name_the_same_page_compare_equal(a, b):
    assert rec.url_key(a) == rec.url_key(b)


def test_a_query_that_names_the_document_is_kept():
    a = "https://eur-lex.europa.eu/legal-content/IT/ALL/?uri=celex%3A32010L0063"
    b = "https://eur-lex.europa.eu/legal-content/IT/ALL/?uri=celex%3A32010L0064"
    assert rec.url_key(a) != rec.url_key(b)


def test_the_same_url_and_title_is_already_present():
    pool = [dict(item("REG", REGISTER["title"]), url="http://theregister.com/2006/06/26/drug_trial_fingers")]
    rows = plan(StubTranslation({REGISTER_URL: REGISTER}), [{"id": REGISTER_URL}], pool)
    assert rows[0].outcome == "present"
    assert rows[0].existing["match"] == "url"


def test_the_same_url_with_another_title_is_asked_about():
    pool = [dict(item("CH1", "Another chapter entirely"), url=REGISTER_URL)]
    rows = plan(StubTranslation({REGISTER_URL: REGISTER}), [{"id": REGISTER_URL}], pool)
    assert rows[0].outcome == "ambiguous"
    assert rows[0].candidates[0]["key"] == "CH1"
    assert "same URL" in rows[0].warnings[-1]


# --- inside one plan ---------------------------------------------------------------------


def test_the_same_paper_twice_in_one_plan_goes_in_once():
    """A DOI and the PubMed Central URL of the same paper."""
    paper = {"itemType": "journalArticle", "title": "At the Cutting Edge of the Impossible",
             "DOI": "10.1234/thij", "creators": []}
    pmc = "https://pmc.ncbi.nlm.nih.gov/articles/PMC2763473/"
    tr = StubTranslation({"10.1234/thij": paper, pmc: paper})
    rows = plan(tr, [{"id": pmc, "ref_key": "15-21b"}], identifiers=["10.1234/thij"])
    assert rows[0].outcome == "new"
    assert rows[1].outcome == "present"
    assert rows[1].existing == {"plan_row": 0, "identifier": "10.1234/thij", "match": "doi"}
    assert ing._skip_reason(rows[1], False) == "the same item comes earlier in this plan"


def test_two_sources_with_no_identifier_and_the_same_title_are_asked_about():
    e = {"item_type": "webpage", "metadata": {"title": "Ban on animal testing"}}
    rows = plan(FailingTranslation({}), [e, dict(e, metadata={"title": "Ban on animal testing",
                                                                "date": "2013"})])
    assert rows[0].outcome == "new"
    assert rows[1].outcome == "ambiguous"
    assert rows[1].candidates[0]["plan_row"] == 0


# --- the checks that were already there still run -----------------------------------------


def test_the_hint_still_checks_a_caller_record_against_the_translator():
    rows = plan(StubTranslation({REGISTER_URL: REGISTER}), [{
        "id": REGISTER_URL, "hint": "Gershon", "item_type": "newspaperArticle"}])
    assert any("copied wrong" in w for w in rows[0].warnings)


def test_a_plain_identifier_still_works_alongside_entries():
    rows = plan(StubTranslation({REGISTER_URL: REGISTER}), [], identifiers=[REGISTER_URL])
    assert rows[0].outcome == "new" and rows[0].source == "translator"
    assert any("translator called this" in w for w in rows[0].warnings)


# --- writing ------------------------------------------------------------------------------


def test_caller_metadata_is_tagged_and_translator_output_is_not():
    rows = plan(StubTranslation({REGISTER_URL: REGISTER}), [
        {"id": REGISTER_URL, "item_type": "newspaperArticle"},
    ], identifiers=["https://www.who.int/x"])
    caller = rows[1]
    sent = ing._to_zotero_item(caller, "BBB")
    assert {"tag": rec.CALLER_TAG} in sent["tags"]
    plain = ing.Row(identifier="u", raw={"itemType": "webpage", "title": "t"})
    assert "tags" not in ing._to_zotero_item(plain, "BBB")


def test_both_tags_together_with_provenance():
    rows = plan(StubTranslation({REGISTER_URL: REGISTER}),
                [{"id": REGISTER_URL, "item_type": "newspaperArticle"}])
    sent = ing._to_zotero_item(rows[0], "BBB", {"verdict": "supports"})
    assert {"tag": rec.CALLER_TAG} in sent["tags"]
    assert {"tag": "catena:supports"} in sent["tags"]


def test_invalid_rows_are_never_written():
    r = ing.Row(identifier="x", outcome="invalid", warnings=["bad field"])
    assert ing._skip_reason(r, force_ambiguous=True).endswith("bad field")


def test_a_plan_saved_before_this_change_still_loads():
    old = {"identifier": "10.1/x", "kind": "doi", "outcome": "new", "item_type": "book",
           "metadata": {}, "existing": None, "candidates": [], "hint": None,
           "warnings": [], "raw": {"itemType": "book", "title": "t"}}
    row = ing.Row(**old)
    assert row.source == "translator" and row.ref_key is None
    assert json.loads(json.dumps(row.as_dict()))["source"] == "translator"
