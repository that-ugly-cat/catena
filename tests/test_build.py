"""
Building fields — SPEC §7, and the half that `fields.py` reads back.

The interesting assertions here are the ones tied to something observed rather
than to something decided. The 255/50 split is what Zotero itself produced in
the real manuscript; the grouped form is what Word rendered as (1,2) and
(Bonham et al., 2009; Rosato et al., 2008a); the refusals are the cases where
being wrong is silent, which is the only reason they raise instead of coping.
"""

from __future__ import annotations

import json

import pytest

from catena import build
from catena.fields import MARK_ITEM

CSL = {
    "id": "22892531/EB734IKH",
    "type": "article-journal",
    "title": "Community participation: lessons for maternal, newborn, and child health",
    "issued": {"date-parts": [[2008]]},
    "DOI": "10.1016/S0140-6736(08)61406-3",
}
LIB = "groups/6378365"


def decode(field: str) -> dict:
    assert field.startswith(MARK_ITEM), "the reader's marker must match the writer's"
    return json.loads(field[len(MARK_ITEM) :])


# --- identifiers -------------------------------------------------------------


def test_citation_ids_are_eight_characters_and_never_repeat():
    """Unique per occurrence, not per item (SPEC §7.1)."""
    ids = {build.new_citation_id() for _ in range(500)}
    assert len(ids) == 500
    assert all(len(i) == 8 for i in ids)


def test_a_local_profile_uri_is_refused():
    """Those resolve on one machine in the world and orphan every co-author."""
    with pytest.raises(ValueError, match="local-profile"):
        build.item_uri("users/local/etkLASSq", "NE3IZD4R")


def test_uri_shape():
    assert build.item_uri(LIB, "EB734IKH") == (
        "http://zotero.org/groups/6378365/items/EB734IKH"
    )


# --- the citation ------------------------------------------------------------


def test_a_single_citation_carries_uris_and_embedded_data():
    obj = decode(build.citation_field([build.citation_item(LIB, "EB734IKH", CSL)]))
    (item,) = obj["citationItems"]
    assert item["uris"] == ["http://zotero.org/groups/6378365/items/EB734IKH"]
    assert item["itemData"]["title"].startswith("Community participation")
    assert obj["properties"]["noteIndex"] == 0
    assert obj["schema"].endswith("csl-citation.json")


def test_two_items_make_one_grouped_field():
    """SPEC §11.2 item 12: (1,2), one field — not two adjacent ones.

    Not an edge case: 45% of the citation parentheses in the calibration draft
    grouped two or more references, one of them seven.
    """
    obj = decode(
        build.citation_field(
            [
                build.citation_item(LIB, "EB734IKH", CSL),
                build.citation_item(LIB, "E8MZTK9U", dict(CSL, id="x/E8MZTK9U")),
            ]
        )
    )
    assert len(obj["citationItems"]) == 2


def test_visible_text_is_empty_by_default():
    """One Refresh in Word fills it in, numbering and grouping included.

    The API cannot: it renders each item alone, so a numeric style comes back as
    (1) for everything (SPEC §7.5, measured).
    """
    obj = decode(build.citation_field([build.citation_item(LIB, "K", CSL)]))
    assert obj["properties"]["formattedCitation"] == ""
    assert obj["properties"]["plainCitation"] == ""


def test_locator_needs_a_label_citeproc_knows():
    with pytest.raises(ValueError, match="unknown locator label"):
        build.citation_item(LIB, "K", CSL, locator="3", label="chapitre")


def test_a_label_without_a_locator_says_nothing():
    with pytest.raises(ValueError):
        build.citation_item(LIB, "K", CSL, label="page")


def test_locator_defaults_to_page():
    item = build.citation_item(LIB, "K", CSL, locator="14")
    assert item["locator"] == "14" and item["label"] == "page"


def test_footnote_styles_are_refused_rather_than_half_written():
    """SPEC §7.6: no verified exemplar, so no guessing."""
    with pytest.raises(ValueError, match="footnote"):
        build.citation_field([build.citation_item(LIB, "K", CSL)], note_index=3)


def test_an_empty_citation_is_not_a_citation():
    with pytest.raises(ValueError):
        build.citation_field([])


# --- document preferences ----------------------------------------------------


def test_prefs_split_exactly_as_zotero_splits_them():
    """255 and 50 unescaped characters — measured on the real manuscript.

    The limit is on the value, not on its XML serialisation, so the raw string
    is chunked first and each piece escaped after (SPEC §7.4).
    """
    prefs = build.document_prefs("http://www.zotero.org/styles/vancouver")
    assert [len(v) for _, v in prefs] == [255, 50]
    assert [n for n, _ in prefs] == ["ZOTERO_PREF_1", "ZOTERO_PREF_2"]


def test_prefs_reassemble_into_the_expected_xml():
    prefs = build.document_prefs("http://www.zotero.org/styles/apa", "it-IT")
    raw = "".join(v for _, v in prefs)
    assert 'name="fieldType" value="Field"' in raw
    assert 'locale="it-IT"' in raw
    assert raw.startswith("<data") and raw.endswith("</data>")


def test_a_style_that_is_not_a_url_is_refused():
    with pytest.raises(ValueError):
        build.document_prefs("apa")


def test_custom_properties_escape_after_splitting():
    prefs = build.document_prefs("http://www.zotero.org/styles/vancouver")
    xml = build.custom_properties_xml(prefs)
    assert "&lt;data" in xml, "the value is escaped in the XML"
    assert xml.count("<vt:lpwstr>") == 2
    assert 'pid="2"' in xml and 'pid="3"' in xml
