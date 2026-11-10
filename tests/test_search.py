import pytest

from customs_bot.glossary import translate
from customs_bot.refdb import ReferenceDB


@pytest.fixture(scope="module")
def db():
    from conftest import ROOT
    return ReferenceDB(ROOT / "data" / "reference.db")


@pytest.mark.parametrize("query,expected,top", [
    ("فاصوليا بيضاء", "0713330090", 2),
    ("ذرة رفيعة", "1007900000", 2),
    ("سرغوم", "1007900000", 2),
    ("ينسون", "0909610000", 2),
    ("كركديه", "1211900016", 3),
    ("جبنة رومي", "0406901010", 1),
    ("موبايل", "8517130000", 3),
    ("لاب توب", "8471300090", 1),
    ("سيارات ملاكي", "8703233090", 6),
    ("حذاء رياضي", "6404110000", 4),
    ("زيت زيتون", "1509301000", 3),
    ("white beans", "0713330090", 4),
    ("sorghum", "1007900000", 2),
    ("anise", "0909610000", 2),
    ("mobile phones", "8517130000", 2),
    ("drilling machines wood", "8465950000", 1),
])
def test_search_finds_expected_line(db, query, expected, top):
    items, total = db.search(query, limit=top)
    assert expected in [i.code for i in items], (query, [i.code for i in items])


def test_parent_lines_hidden_from_search_but_resolvable(db):
    items, _ = db.search("olive oil", limit=10)
    assert all(i.taxes for i in items)
    assert db.is_parent("1509300000")
    kids = db.children("1509300000")
    assert {k.code for k in kids} >= {"1509301000", "1509309000"}


def test_prefix_and_siblings(db):
    items, total = db.by_prefix("071333", 10)
    assert total == 4
    sibs = db.siblings("0713330090")
    assert "0713330030" in [s.code for s in sibs]


def test_gpc_lookup_and_search(db):
    b = db.gpc_get("10007820")
    assert b["class_title"] == "Edible Herbs" and b["segment_title"] == "Crops"
    bricks, total = db.gpc_search("chamomile")
    assert total > 0 and any("Chamomile" in x["title"] for x in bricks)
    bricks, _ = db.gpc_search(translate("فاصوليا بيضا"))
    assert any("Beans" in x["title"] for x in bricks)
