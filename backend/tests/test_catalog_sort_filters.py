"""
The Catalog page sorts and filters on the SERVER (owner 2026-10-12)
==================================================================
Mockup artboard Main: Sort by (Recently edited is the default), Brand, Frame
colour, Lens colour, Gender, Shape, Frame type, Material, Added by, Last
edited by, Date added / Last edited ranges in INDIA days, Flags, Include
inactive -- AND-ed, every one optional. GET /products routes any of them
through services/catalog_list: ONE query for the page and its count, sorted
across the whole result with product_id as the tiebreak.

Pinned here, against a real Mongo engine (CI's container; mongomock on a dev
box), with every expected set written out BY HAND:
  * each filter alone and combined; options match trimmed + case-insensitive
  * each sort across several pages: no row repeats, none is skipped
  * IST day edges: 00:10 IST belongs to that IST day, 23:50 IST to its own
  * every new parameter is in the list cache key (#1161 / #1170 lesson)
  * a facet count equals the list count for that option alone
  * the Added column's names come from users first, the stored name second
  * archived products show only under Flags: Archived, and only to admins
  * a caller sending none of the new parameters gets the legacy list

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_catalog_sort_filters.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import dependencies as deps  # noqa: E402
from api.routers import products as products_mod  # noqa: E402
from api.services import catalog_list  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402

ADMIN = {"user_id": "u-admin", "username": "admin", "full_name": "Avinash", "roles": ["ADMIN"]}
CATALOGUER = {"user_id": "u-mansi", "username": "mansi", "roles": ["CATALOG_MANAGER"]}
CASHIER = {"user_id": "u-cash", "username": "cash", "roles": ["CASHIER"]}


def utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi)


# IST = UTC + 5:30. 2026-10-12 00:10 IST == 2026-10-11 18:40 UTC.
DOCS = [
    {
        "product_id": "P01", "sku": "SG-CAR-1", "name": "Carrera 8035 Black", "brand": "Carrera",
        "model": "8035", "category": "SUNGLASS", "mrp": 12990, "is_active": True,
        "attributes": {"frame_color": "Black", "lens_colour": "Grey", "gender": "Men",
                       "shape": "Square", "frame_type": "Full Rim", "frame_material": "Acetate"},
        "created_by": "u-mansi", "created_by_name": "mansi", "created_at": utc(2026, 10, 11, 18, 40),
        "updated_at": utc(2026, 10, 11, 18, 40),
    },
    {
        "product_id": "P02", "sku": "SG-RB-1", "name": "Ray-Ban Justin", "brand": "Ray-Ban",
        "model": "4165", "category": "SUNGLASS", "mrp": 9490, "is_active": True,
        "attributes": {"frame_color": " black ", "lens_colour": "Green", "gender": "Unisex",
                       "shape": "Rectangle", "frame_type": "Full Rim", "frame_material": "Nylon"},
        "created_by": "u-mansi", "created_by_name": "mansi", "created_at": utc(2026, 10, 11, 18, 20),
        "updated_at": utc(2026, 10, 12, 6, 0), "updated_by": "u-akshay", "updated_by_name": "akshay",
    },
    {
        "product_id": "P03", "sku": "SG-OAK-1", "name": "oakley radar", "brand": "Oakley",
        "model": "9208", "category": "SUNGLASS", "mrp": 17350, "is_active": True,
        "attributes": {"frame_color": "Gold", "gender": "Men", "shape": "Aviator",
                       "frame_type": "Half Rim", "frame_material": "Metal"},
        "created_by": "u-akshay", "created_by_name": "akshay", "created_at": utc(2026, 10, 1, 5, 0),
        "updated_at": utc(2026, 10, 1, 5, 0),
        "flags": [{"flag_id": "f1", "type": "price_check", "status": "open", "note": "MRP looks low"}],
    },
    {
        # Never edited, no updated_at at all: Recently edited counts its add date.
        "product_id": "P04", "sku": "FR-POL-1", "name": "Polaroid 2101", "brand": "Polaroid",
        "model": "2101", "category": "FRAME", "mrp": 4990, "is_active": True,
        "attributes": {"frame_color": "Havana", "gender": "Women", "shape": "Round",
                       "frame_type": "Full Rim", "frame_material": "Acetate"},
        "created_by": "u-mansi", "created_by_name": "mansi", "created_at": utc(2026, 10, 10, 10, 35),
        "flags": [
            {"flag_id": "f2", "type": "photo_redo", "status": "open", "note": "Front photo is 3rd"},
            {"flag_id": "f3", "type": "price_check", "status": "fixed"},
        ],
    },
    {
        "product_id": "P05", "sku": "SG-CAR-2", "name": "Carrera 8036 Gold", "brand": "carrera ",
        "model": "8036", "category": "SUNGLASS", "mrp": 12990, "is_active": False,
        "attributes": {"frame_color": "Gold", "gender": "Men"},
        "created_by": "u-akshay", "created_by_name": "akshay", "created_at": utc(2026, 9, 30, 12, 0),
        "updated_at": utc(2026, 10, 2, 12, 0), "updated_by": "u-mansi", "updated_by_name": "mansi",
    },
    {
        # Archived (soft-deleted): only Flags: Archived shows it, admins only.
        "product_id": "P06", "sku": "SG-MJ-1", "name": "Maui Jim Peahi", "brand": "Maui Jim",
        "model": "202", "category": "SUNGLASS", "mrp": 28900, "is_active": False,
        "attributes": {"frame_color": "Black", "gender": "Men"},
        "created_by": "u-gone", "created_by_name": "Old Hand", "created_at": utc(2026, 9, 1, 5, 0),
        "updated_at": utc(2026, 10, 5, 5, 0), "deleted_at": "2026-10-05T05:00:00+00:00",
    },
    {
        "product_id": "P07", "sku": "SG-EA-1", "name": "Emporio Armani 4115", "brand": "Emporio Armani",
        "model": "4115", "category": "SUNGLASS", "mrp": 11200, "is_active": True,
        "attributes": {"frame_color": "BLACK", "gender": "Men", "frame_material": "Acetate"},
        "created_by": "u-akshay", "created_by_name": "akshay", "created_at": utc(2026, 10, 9, 10, 0),
        "updated_at": utc(2026, 10, 9, 10, 0),
    },
]

USERS = [
    {"user_id": "u-mansi", "username": "mansi", "full_name": "Mansi Kumari", "is_active": True},
    {"user_id": "u-akshay", "username": "akshay", "full_name": "Akshay Ovhal", "is_active": True},
]


class _Conn:
    """The dependencies.get_db() shape over a real / mongomock database."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]


_CLIENT = []


def _open_db():
    """A real Mongo when one answers (CI), else mongomock. Probed ONCE."""
    name = f"ims_test_catsort_{uuid.uuid4().hex[:8]}"
    if not _CLIENT:
        from pymongo import MongoClient

        uri = os.getenv("MONGODB_URL") or os.getenv("MONGODB_URI") or "mongodb://localhost:27017"
        try:
            client = MongoClient(uri, serverSelectionTimeoutMS=1500)
            client.server_info()
        except Exception:
            try:
                import mongomock
            except ImportError:
                pytest.skip("no Mongo and no mongomock available")
            client = mongomock.MongoClient()
        _CLIENT.append(client)
    return _CLIENT[0], name


@pytest.fixture
def db(monkeypatch):
    client, name = _open_db()
    db = client[name]
    db["products"].insert_many([dict(d) for d in DOCS])
    db["users"].insert_many([dict(u) for u in USERS])
    conn = _Conn(db)
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: ProductRepository(db["products"]))
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    yield db
    try:
        client.drop_database(name)
    except Exception:
        pass


class _RecordingCache:
    """A real (dict) cache that also records every key it was asked for."""

    TTL_MEDIUM = 300

    def __init__(self):
        self.store = {}
        self.keys = []

    def get(self, k):
        self.keys.append(k)
        return self.store.get(k)

    def set(self, k, v, ttl=0):
        import copy

        self.store[k] = copy.deepcopy(v)

    def delete_pattern(self, pattern):
        self.store.clear()


@pytest.fixture(autouse=True)
def cache(monkeypatch):
    import api.services.cache as cache_mod

    c = _RecordingCache()
    monkeypatch.setattr(cache_mod, "cache", c)
    return c


_DEFAULTS = {
    "category": None, "brand": None, "search": None, "match": None, "tag": None,
    "created_by": None, "store_id": None, "skip": 0, "limit": 50, "is_active": None,
    "photo": None, "sort": None, "frame_color": None, "lens_colour": None, "gender": None,
    "shape": None, "frame_type": None, "frame_material": None, "updated_by": None,
    "date_on": None, "date_from": None, "date_to": None, "flag": None,
}


def _list(user=ADMIN, **kw):
    params = dict(_DEFAULTS, current_user=user)
    params.update(kw)
    return asyncio.run(products_mod.list_products(**params))


def _ids(out):
    return [p["product_id"] for p in out["products"]]


# ---------------------------------------------------------------------------
# Filters -- alone, combined, trimmed + case-insensitive
# ---------------------------------------------------------------------------


def test_frame_colour_matches_trimmed_and_case_insensitive(db):
    out = _list(sort="name_asc", is_active="true", frame_color="black")
    assert _ids(out) == ["P01", "P07", "P02"]  # 'Black', 'BLACK', ' black '; P06 archived
    assert out["total_count"] == 3


@pytest.mark.parametrize(
    "param,value,expected",
    [
        ("brand", "CARRERA", ["P01"]),  # P05 'carrera ' is inactive
        ("lens_colour", "green", ["P02"]),
        ("gender", "men", ["P01", "P03", "P07"]),
        ("shape", "Round", ["P04"]),
        ("frame_type", "half rim", ["P03"]),
        ("frame_material", "ACETATE", ["P01", "P04", "P07"]),
        ("created_by", "u-akshay", ["P03", "P07"]),
        ("updated_by", "u-akshay", ["P02"]),
        ("updated_by", "none", ["P01", "P03", "P04", "P07"]),
        ("category", "FRAME", ["P04"]),
    ],
)
def test_each_filter_alone(db, param, value, expected):
    out = _list(sort="oldest", is_active="true", **{param: value})
    assert sorted(_ids(out)) == sorted(expected)
    assert out["total_count"] == len(expected)


def test_filters_are_anded(db):
    out = _list(sort="name_asc", is_active="true", frame_color="Black", gender="Men",
                frame_material="acetate", created_by="u-akshay")
    assert _ids(out) == ["P07"]
    out = _list(sort="name_asc", is_active="true", frame_color="Black", created_by="u-akshay",
                lens_colour="Grey")
    assert _ids(out) == []
    assert out["total_count"] == 0


def test_regex_text_is_escaped(db):
    out = _list(sort="name_asc", is_active="all", frame_color=".*")
    assert _ids(out) == []


def test_include_inactive_shows_inactive_but_never_archived(db):
    out = _list(sort="name_asc", is_active="all", brand="carrera")
    assert _ids(out) == ["P01", "P05"]
    out = _list(sort="name_asc", is_active="all")
    assert "P06" not in _ids(out)
    assert out["total_count"] == 6


def test_search_keeps_the_tills_word_rule_inside_the_filters(db):
    out = _list(sort="name_asc", is_active="true", search="carr", frame_color="black")
    assert _ids(out) == ["P01"]
    # 'adar' is inside 'radar' but starts no word: the till's rule says no.
    assert _ids(_list(sort="name_asc", is_active="true", search="adar")) == []


# ---------------------------------------------------------------------------
# Flags filter + archived (admins only)
# ---------------------------------------------------------------------------


def test_flag_filters(db):
    assert sorted(_ids(_list(sort="name_asc", is_active="true", flag="any"))) == ["P03", "P04"]
    assert _ids(_list(sort="name_asc", is_active="true", flag="price_check")) == ["P03"]
    assert _ids(_list(sort="name_asc", is_active="true", flag="photo_redo")) == ["P04"]
    # Mine = open flags on products the signed-in user ADDED.
    assert _ids(_list(user=CATALOGUER, sort="name_asc", is_active="true", flag="mine")) == ["P04"]
    assert _ids(_list(sort="name_asc", is_active="true", flag="mine")) == []


def test_archived_is_admins_only_and_shows_only_there(db):
    out = _list(sort="name_asc", is_active="true", flag="archived")
    assert _ids(out) == ["P06"]
    with pytest.raises(HTTPException) as exc:
        _list(user=CATALOGUER, sort="name_asc", flag="archived")
    assert exc.value.status_code == 403


def test_the_tills_search_never_shows_an_archived_product(db):
    # The legacy search path (POS): active only, so the archive hides it.
    out = _list(user=CASHIER, search="Maui")
    assert _ids(out) == []


# ---------------------------------------------------------------------------
# Sorts -- server side, whole result, stable tiebreak, no repeat / skip
# ---------------------------------------------------------------------------

EXPECTED_ORDER = {
    # Active, not archived: P01 P02 P03 P04 P07. Written out by hand.
    "edited": ["P02", "P01", "P04", "P07", "P03"],
    "newest": ["P01", "P02", "P04", "P07", "P03"],
    "oldest": ["P03", "P07", "P04", "P02", "P01"],
    "name_asc": ["P01", "P07", "P03", "P04", "P02"],
    "name_desc": ["P02", "P04", "P03", "P07", "P01"],
    "brand_asc": ["P01", "P07", "P03", "P04", "P02"],
    "mrp_desc": ["P03", "P01", "P07", "P02", "P04"],
    "mrp_asc": ["P04", "P02", "P07", "P01", "P03"],
}


@pytest.mark.parametrize("sort", sorted(EXPECTED_ORDER))
def test_each_sort_pages_without_repeat_or_skip(db, sort):
    seen = []
    for skip in (0, 2, 4):
        out = _list(sort=sort, is_active="true", skip=skip, limit=2)
        assert out["total_count"] == 5
        seen += _ids(out)
    assert seen == EXPECTED_ORDER[sort]


def test_equal_sort_keys_break_ties_on_product_id(db):
    # Stored in REVERSE id order with one MRP: only the tiebreak orders them.
    docs = list(db["products"].find({}, {"_id": 0}))
    db["products"].delete_many({})
    db["products"].insert_many([dict(d, mrp=1000) for d in reversed(docs)])
    seen = []
    for skip in (0, 2, 4):
        seen += _ids(_list(sort="mrp_desc", is_active="true", skip=skip, limit=2))
    assert seen == ["P01", "P02", "P03", "P04", "P07"]


def test_recently_edited_is_the_default_once_any_new_parameter_is_sent(db):
    out = _list(is_active="true", gender="men")
    assert _ids(out) == ["P01", "P07", "P03"]


# ---------------------------------------------------------------------------
# Dates -- INDIA days, both ends included
# ---------------------------------------------------------------------------


def test_a_product_added_at_0010_ist_belongs_to_that_ist_day(db):
    # P01: 18:40 UTC on 11 Oct == 00:10 IST on 12 Oct.
    # P02: 18:20 UTC on 11 Oct == 23:50 IST on 11 Oct.
    out = _list(sort="name_asc", is_active="true", date_on="added",
                date_from="2026-10-12", date_to="2026-10-12")
    assert _ids(out) == ["P01"]
    out = _list(sort="name_asc", is_active="true", date_on="added",
                date_from="2026-10-11", date_to="2026-10-11")
    assert _ids(out) == ["P02"]


def test_both_days_are_included(db):
    out = _list(sort="oldest", is_active="true", date_on="added",
                date_from="2026-10-01", date_to="2026-10-10")
    assert _ids(out) == ["P03", "P07", "P04"]


def test_last_edited_range_counts_a_never_edited_row_by_its_add_date(db):
    # P04 has no updated_at: added 10 Oct 16:05 IST.
    out = _list(sort="name_asc", is_active="true", date_on="edited",
                date_from="2026-10-10", date_to="2026-10-10")
    assert _ids(out) == ["P04"]
    # P02 was added 11 Oct but edited 12 Oct 11:30 IST.
    out = _list(sort="name_asc", is_active="true", date_on="edited",
                date_from="2026-10-12", date_to="2026-10-12")
    assert _ids(out) == ["P01", "P02"]


def test_from_after_to_is_refused_in_plain_words(db):
    with pytest.raises(HTTPException) as exc:
        _list(sort="edited", date_from="2026-10-12", date_to="2026-10-01")
    assert exc.value.status_code == 400
    assert "From date is after the To date" in exc.value.detail


# ---------------------------------------------------------------------------
# Cache key -- every new parameter is in it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "param,a,b",
    [
        ("sort", "name_asc", "name_desc"),
        ("frame_color", "Black", "Gold"),
        ("lens_colour", "Grey", "Green"),
        ("gender", "Men", "Women"),
        ("shape", "Square", "Round"),
        ("frame_type", "Full Rim", "Half Rim"),
        ("frame_material", "Acetate", "Metal"),
        ("updated_by", "u-akshay", "none"),
        ("date_on", "added", "edited"),
        ("date_from", "2026-10-01", "2026-10-10"),
        ("date_to", "2026-10-11", "2026-10-12"),
        ("flag", "any", "price_check"),
    ],
)
def test_two_different_choices_never_share_a_cached_page(db, cache, param, a, b):
    base = {"sort": "edited", "is_active": "true"}
    if param == "date_on":
        base.update(date_from="2026-10-10", date_to="2026-10-12")
    _list(**dict(base, **{param: a}))
    _list(**dict(base, **{param: b}))
    assert len(set(cache.keys)) == 2, cache.keys


def test_flagged_on_my_entries_is_cached_per_person(db, cache):
    mine_admin = _list(sort="edited", is_active="true", flag="mine")
    mine_mansi = _list(user=dict(CATALOGUER, roles=["ADMIN"]), sort="edited", is_active="true", flag="mine")
    assert _ids(mine_admin) == [] and _ids(mine_mansi) == ["P04"]


def test_a_cached_page_answers_the_same_choice(db, cache):
    first = _list(sort="name_asc", is_active="true", gender="Men")
    db["products"].delete_many({})
    again = _list(sort="name_asc", is_active="true", gender="Men")
    assert _ids(again) == _ids(first)


# ---------------------------------------------------------------------------
# Facets -- options with counts that equal the list
# ---------------------------------------------------------------------------


def _facets(user=ADMIN, is_active=None):
    return asyncio.run(products_mod.catalog_facets(is_active=is_active, current_user=user))


def test_facet_options_and_counts(db):
    f = _facets()
    assert f["total"] == 5
    colours = {o["value"].lower(): o["count"] for o in f["frame_color"]}
    assert f["frame_color"][0]["count"] == 3  # most common first
    assert colours == {"black": 3, "gold": 1, "havana": 1}
    assert {o["value"]: o["count"] for o in f["gender"]} == {"Men": 3, "Unisex": 1, "Women": 1}
    assert f["created_by"] == [
        {"value": "u-mansi", "label": "Mansi Kumari", "count": 3},
        {"value": "u-akshay", "label": "Akshay Ovhal", "count": 2},
    ]
    assert f["updated_by"] == [
        {"value": "u-akshay", "label": "Akshay Ovhal", "count": 1},
        {"value": "none", "label": "Never edited", "count": 4},
    ]
    assert f["flags"] == {"open": 2, "mine": 0, "by_type": {"price_check": 1, "photo_redo": 1}, "archived": 1}


def test_facet_counts_equal_the_filtered_list_counts(db):
    for include in (None, "all"):
        f = _facets(is_active=include)
        list_active = "all" if include == "all" else "true"
        for param in ("brand", *catalog_list.ATTRIBUTE_FILTERS):
            for opt in f[param]:
                out = _list(sort="edited", is_active=list_active, **{param: opt["value"]})
                assert out["total_count"] == opt["count"], (param, opt)
        for param in ("created_by", "updated_by"):
            for opt in f[param]:
                out = _list(sort="edited", is_active=list_active, **{param: opt["value"]})
                assert out["total_count"] == opt["count"], (param, opt)
        for t, n in f["flags"]["by_type"].items():
            assert _list(sort="edited", is_active=list_active, flag=t)["total_count"] == n


def test_the_archived_count_is_admins_only(db):
    assert "archived" not in _facets(user=CATALOGUER)["flags"]
    assert _facets(user=CATALOGUER)["flags"]["mine"] == 1


# ---------------------------------------------------------------------------
# The Added column's names: users first, the stored name second
# ---------------------------------------------------------------------------


def test_added_and_edited_names_come_from_users_then_the_stored_name(db):
    rows = {p["product_id"]: p for p in _list(sort="edited", is_active="all")["products"]}
    assert rows["P02"]["created_by_name"] == "Mansi Kumari"
    assert rows["P02"]["updated_by_name"] == "Akshay Ovhal"
    assert rows["P05"]["updated_by_name"] == "Mansi Kumari"
    archived = _list(sort="edited", flag="archived")["products"][0]
    assert archived["created_by_name"] == "Old Hand"  # u-gone is not in users


def test_counter_roles_get_no_attribution_and_no_flags(db):
    rows = _list(user=CASHIER, search="Oakley")["products"]
    assert rows and "flags" not in rows[0] and "created_by" not in rows[0]


# ---------------------------------------------------------------------------
# Every other caller: the legacy list, unchanged
# ---------------------------------------------------------------------------


def test_no_new_parameter_keeps_the_legacy_default_path(db):
    out = _list()
    # Legacy default path: inactive AND archived rows included, insertion order.
    assert sorted(_ids(out)) == ["P01", "P02", "P03", "P04", "P05", "P06", "P07"]
    assert out["total_count"] == 7
