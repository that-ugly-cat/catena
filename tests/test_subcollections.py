"""
A binding whose deposit collection has subcollections — one per chapter of a book.

Zotero's API reads one collection at a time and never includes the ones below
it, so everything here is about seeing the whole tree: dedup across chapters,
filing an item one chapter already holds into another instead of copying it,
and moving items that sit loose in the book's collection into a chapter. The
Zotero side is a fake that keeps item collections and versions the way the API
does; the tree walk and the decisions are the real code.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from catena.server import ingest as ing
from catena.server.models import SessionLocal, init_db
from catena.server.zotero_client import Zot, ZoteroError, pick_collection, subtree

from test_ingest import ROSATO, FakeBinding, StubTranslation

init_db()

LIB = "groups/2"
ROOT, C15, C31, OTHER = "ROOT0001", "C15AAAAA", "C31AAAAA", "OTHER001"
COLLECTIONS = [
    {"key": ROOT, "name": "Bioetica da Bar — libro", "parent": None, "items": 0},
    {"key": C31, "name": "31 Il corpo degli altri", "parent": ROOT, "items": 0},
    {"key": C15, "name": "15 La scienza non è vegan", "parent": ROOT, "items": 0},
    {"key": "C15SUB01", "name": "15a fonti di contorno", "parent": C15, "items": 0},
    {"key": OTHER, "name": "ambizione v6", "parent": None, "items": 0},
    {"key": "OTHER002", "name": "31 Il corpo degli altri copia", "parent": OTHER, "items": 0},
]
DOI = "10.1016/S0140-6736(08)61406-3"


class FakeZot(Zot):
    """Items with collections and versions; writes recorded, versions bumped."""

    def __init__(self, items: dict[str, dict], collections=COLLECTIONS):
        super().__init__("fake")
        self.store = items
        self.cols = collections
        self.writes: list[tuple[str, list[str]]] = []
        self.fail: set[str] = set()

    def collections(self, library):
        return self.cols

    def collection_items(self, library, collection_key, limit=200):
        return [dict(it, collections=list(it["collections"]))
                for it in self.store.values() if collection_key in it["collections"]]

    def items(self, library, keys):
        return [dict(self.store[k], collections=list(self.store[k]["collections"]))
                for k in keys if k in self.store]

    def set_collections(self, library, key, version, collections):
        if key in self.fail or version != self.store[key]["version"]:
            raise ZoteroError(f"{library}/{key} changed since it was read")
        self.store[key]["collections"] = list(collections)
        self.store[key]["version"] += 1
        self.writes.append((key, list(collections)))


def zitem(key, title, cols, doi=None, version=5):
    return {"key": key, "library": LIB, "title": title, "doi": doi, "year": "2008",
            "first_author": "X", "csljson": {}, "collections": list(cols),
            "version": version}


def binding(source_key=None):
    return FakeBinding(source_library="users/9", source_collection_key=source_key,
                       deposit_library=LIB, deposit_collection_key=ROOT)


# --- the tree ------------------------------------------------------------------


def test_the_tree_is_the_root_and_everything_below_it():
    keys = [c["key"] for c in subtree(COLLECTIONS, ROOT)]
    assert keys[0] == ROOT
    assert set(keys) == {ROOT, C15, C31, "C15SUB01"}


def test_a_vanished_root_says_so():
    with pytest.raises(ZoteroError, match="not in this library"):
        subtree(COLLECTIONS, "GONE0000")


def test_tree_items_sees_subcollections_and_merges_by_key():
    z = FakeZot({
        "LOOSE001": zitem("LOOSE001", "loose", [ROOT]),
        "BOTH0001": zitem("BOTH0001", "in two chapters", [C15, C31]),
        "DEEP0001": zitem("DEEP0001", "deep", ["C15SUB01"]),
        "ELSE0001": zitem("ELSE0001", "elsewhere", [OTHER]),
    })
    got = {it["key"]: it for it in z.tree_items(LIB, ROOT)}
    assert set(got) == {"LOOSE001", "BOTH0001", "DEEP0001"}
    assert set(got["BOTH0001"]["collections"]) == {C15, C31}


# --- naming a collection -----------------------------------------------------------


@pytest.mark.parametrize("wanted", ["15 la scienza non è vegan", C15, "  15 La scienza non è vegan "])
def test_a_chapter_is_found_by_name_or_key(wanted):
    t = ing.resolve_target(FakeZot({}), binding(), wanted)
    assert (t.key, t.is_root) == (C15, False)


def test_no_collection_means_the_deposit_collection():
    t = ing.resolve_target(FakeZot({}), binding(), "")
    assert t.key == ROOT and t.is_root


def test_a_collection_outside_the_binding_is_never_picked():
    with pytest.raises(ValueError, match="below 'Bioetica da Bar — libro'") as e:
        ing.resolve_target(FakeZot({}), binding(), "ambizione v6")
    assert "31 Il corpo degli altri" in str(e.value), "the chapters are listed"


def test_two_chapters_with_one_name_are_not_guessed_between():
    cols = COLLECTIONS + [{"key": "DUP00001", "name": "31 il corpo degli altri",
                           "parent": ROOT, "items": 0}]
    with pytest.raises(ValueError, match="by key"):
        ing.resolve_target(FakeZot({}, cols), binding(), "31 Il corpo degli altri")


def test_a_parent_is_picked_by_key_or_unique_name():
    assert pick_collection(COLLECTIONS, "bioetica da bar — libro", LIB)["key"] == ROOT
    assert pick_collection(COLLECTIONS, C15, LIB)["key"] == C15
    with pytest.raises(ValueError, match="no collection"):
        pick_collection(COLLECTIONS, "nope", LIB)
    dup = COLLECTIONS + [dict(COLLECTIONS[0], key="ROOT0002")]
    with pytest.raises(ValueError, match="name it by key"):
        pick_collection(dup, "Bioetica da Bar — libro", LIB)


# --- planning into a chapter ---------------------------------------------------------


def plan(z, ids, collection, file_present=True, b=None):
    b = b or binding()
    target = ing.resolve_target(z, b, collection)
    rows = ing.build_plan(z, StubTranslation({DOI: ROSATO}), b, ids,
                          target=target, file_present=file_present, schema=object())
    return rows, target


def test_an_item_another_chapter_holds_is_present_and_filed_not_copied():
    z = FakeZot({"ROS00001": zitem("ROS00001", ROSATO["title"], [C15], doi=DOI)})
    rows, _ = plan(z, [DOI], "31 Il corpo degli altri")
    r = rows[0]
    assert r.outcome == "present"
    assert r.file_into == C31
    assert r.existing["in"] == ["15 La scienza non è vegan"]
    assert "collections" not in r.existing
    assert "not a second copy" in r.filing and "31 Il corpo degli altri" in r.filing


def test_file_present_off_leaves_it_where_it_is_and_says_so():
    z = FakeZot({"ROS00001": zitem("ROS00001", ROSATO["title"], [C15], doi=DOI)})
    rows, _ = plan(z, [DOI], C31, file_present=False)
    assert rows[0].outcome == "present" and rows[0].file_into is None
    assert "file_present is off" in rows[0].filing


def test_an_item_already_in_the_chapter_needs_nothing():
    z = FakeZot({"ROS00001": zitem("ROS00001", ROSATO["title"], [C15, C31], doi=DOI)})
    rows, _ = plan(z, [DOI], C31)
    assert rows[0].file_into is None and "nothing to do" in rows[0].filing


def test_a_twin_on_the_source_leg_cannot_be_filed():
    z = FakeZot({})
    b = binding()
    target = ing.resolve_target(z, b, C31)
    pool = [dict(zitem("SRC00001", ROSATO["title"], ["SRCCOLL1"], doi=DOI), library="users/9")]
    rows = ing.build_plan(z, StubTranslation({DOI: ROSATO}), b, [DOI],
                          existing=pool, target=target, schema=object())
    assert rows[0].file_into is None
    assert "source leg" in rows[0].filing


def test_planning_into_the_deposit_collection_itself_behaves_as_before():
    z = FakeZot({"ROS00001": zitem("ROS00001", ROSATO["title"], [C15], doi=DOI)})
    rows, _ = plan(z, [DOI], "")
    assert rows[0].outcome == "present"
    assert rows[0].file_into is None and rows[0].filing is None


def test_dedup_reads_every_chapter():
    z = FakeZot({"ROS00001": zitem("ROS00001", ROSATO["title"], ["C15SUB01"], doi=DOI)})
    rows, _ = plan(z, [DOI], C31)
    assert rows[0].outcome == "present" and rows[0].file_into == C31


# --- applying ----------------------------------------------------------------


class FakeClient:
    def __init__(self):
        self.created = []

    def create_items(self, items):
        self.created += items
        return {"successful": {"0": {"key": f"NEW{len(self.created):05d}"}}}


def test_apply_files_present_items_and_creates_new_ones_where_the_plan_said():
    other_doi = "10.5555/catena-subcollection-test"
    other = dict(ROSATO, DOI=other_doi, title="A wholly different paper")
    z = FakeZot({"ROS00001": zitem("ROS00001", ROSATO["title"], [C15], doi=DOI)})
    b = binding()
    target = ing.resolve_target(z, b, C31)
    rows = ing.build_plan(z, StubTranslation({DOI: ROSATO, other_doi: other}), b,
                          [DOI, other_doi], target=target, schema=object())
    db = SessionLocal()
    try:
        p = ing.save_plan(db, b, rows, target.key)
        client = FakeClient()
        out = ing.apply_plan(db, z, b, p, client=client)
    finally:
        db.close()
    assert [f["key"] for f in out["filed"]] == ["ROS00001"]
    assert z.store["ROS00001"]["collections"] == [C15, C31]
    assert client.created[0]["collections"] == [C31]
    assert len(out["added"]) == 1


def test_a_plan_from_before_subcollections_still_applies_to_the_deposit_collection():
    p = ing.IngestPlan(binding_id=1, payload="[]")
    assert p.collection_key is None
    row = ing.Row(identifier="x", raw={"itemType": "book", "title": "t"})
    assert ing._to_zotero_item(row, ROOT)["collections"] == [ROOT]


# --- filing existing items ------------------------------------------------------


def loose_book():
    return FakeZot({
        "L0000001": zitem("L0000001", "one", [ROOT]),
        "L0000002": zitem("L0000002", "two", [ROOT, OTHER]),
        "C3100001": zitem("C3100001", "three", [C31]),
        "ELSE0001": zitem("ELSE0001", "not the book's", [OTHER]),
    })


def test_move_files_into_the_chapter_and_out_of_the_parent_in_one_write():
    z = loose_book()
    t = ing.resolve_target(z, binding(), C15)
    out = ing.file_items(z, binding(), ["L0000001", "L0000002"], t, move=True)
    assert [f["key"] for f in out["filed"]] == ["L0000001", "L0000002"]
    assert all(f["removed_from_parent"] for f in out["filed"])
    assert z.store["L0000001"]["collections"] == [C15]
    assert z.store["L0000002"]["collections"] == [OTHER, C15], "other memberships stay"
    assert len(z.writes) == 2, "one write per item"


def test_without_move_the_parent_keeps_the_item():
    z = loose_book()
    t = ing.resolve_target(z, binding(), C15)
    ing.file_items(z, binding(), ["L0000001"], t)
    assert z.store["L0000001"]["collections"] == [ROOT, C15]


def test_items_outside_the_binding_are_refused():
    z = loose_book()
    t = ing.resolve_target(z, binding(), C15)
    out = ing.file_items(z, binding(), ["ELSE0001", "MISSING1"], t, move=True)
    assert {r["key"] for r in out["refused"]} == {"ELSE0001", "MISSING1"}
    assert not z.writes


def test_an_item_already_there_is_unchanged_and_not_written():
    z = loose_book()
    t = ing.resolve_target(z, binding(), C31)
    out = ing.file_items(z, binding(), ["C3100001"], t, move=True)
    assert out["unchanged"][0]["key"] == "C3100001" and not z.writes


def test_an_item_edited_since_it_was_read_fails_alone():
    z = loose_book()
    z.fail.add("L0000001")
    t = ing.resolve_target(z, binding(), C15)
    out = ing.file_items(z, binding(), ["L0000001", "L0000002"], t, move=True)
    assert [f["key"] for f in out["failed"]] == ["L0000001"]
    assert [f["key"] for f in out["filed"]] == ["L0000002"]
    assert z.store["L0000001"]["collections"] == [ROOT]


def test_moving_into_the_parent_itself_is_refused():
    z = loose_book()
    t = ing.resolve_target(z, binding(), "")
    assert "error" in ing.file_items(z, binding(), ["L0000001"], t, move=True)


def test_too_many_keys_are_refused_before_anything_is_read():
    z = loose_book()
    t = ing.resolve_target(z, binding(), C15)
    keys = [f"K{i:07d}" for i in range(ing.FILE_LIMIT + 1)]
    assert "at most" in ing.file_items(z, binding(), keys, t)["error"]


# --- the write itself -------------------------------------------------------------


def test_set_collections_sends_only_collections_guarded_by_the_version(monkeypatch):
    sent = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        sent["method"], sent["url"] = req.get_method(), req.full_url
        sent["version"] = req.get_header("If-unmodified-since-version")
        sent["body"] = json.loads(req.data)
        return Resp(b"")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    Zot("k").set_collections(LIB, "ITEM0001", 7, [C15])
    assert sent == {"method": "PATCH",
                    "url": "https://api.zotero.org/groups/2/items/ITEM0001",
                    "version": "7", "body": {"collections": [C15]}}


def test_a_412_is_reported_as_a_changed_item(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 412, "Precondition Failed", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ZoteroError, match="changed since it was read"):
        Zot("k").set_collections(LIB, "ITEM0001", 7, [C15])
