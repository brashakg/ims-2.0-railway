"""
Per-store Shopify locations (owner ruling 2026-09-06) -- PR 1: the mapping
==========================================================================
"Product will be shipped from whichever store holds the inventory": every
physical shop becomes a Shopify location, mapped ON THE STORE RECORD from the
Organization page (stores.<doc>.shopify_location_id / _name). Pinned here,
each REVERT-PROOF (revert the named piece and the test goes red):

  1. PUT persists the field through model_dump(exclude_unset=True) -- drop the
     field from StoreUpdate and pydantic silently discards it.
  2. Bare digits are promoted to gid://shopify/Location/<n>; a malformed value
     is a 400; "" clears the gid AND the display name.
  3. An ONLINE store never takes a location (400): a known online id, a doc
     with store_type ONLINE, and a POST with store_type ONLINE.
  4. A gid already on another store is a 409 naming that store; re-saving a
     store's own gid is not a duplicate.
  5. The display name is copied from Shopify's locations read on save (the
     client's text never wins when the read answers).
  6. stores_util.physical_stores: active AND not ONLINE (known id with no
     store_type, and store_type ONLINE under a new id), includes a UUID
     store_id, sorted by store_code; no DB handle -> [].
  7. scripts/migrate_store_locations.py --set routes through the router's
     validator (ONE rule): the same ONLINE / duplicate refusals; a clean plan
     writes through the store repository; Gangadham Pune (by code OR by
     location number) is refused at plan time unless --i-know-pune.
  8. ONE holder reader: the validator's "who holds this gid" is
     stores_util.physical_stores -- the same list the dropdown joins against
     -- so an ONLINE or inactive doc carrying a gid blocks nobody; flipping a
     mapped shop to ONLINE is 400 until the mapping is cleared; reactivating
     a doc whose gid an active shop now holds is 409 (an unrelated edit on
     the inactive doc is not).

TestClient + StrictDB; no Mongo, no network (the locations read is DARK).
Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_store_shopify_location.py -q
"""

from __future__ import annotations

import importlib.util
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strict_fakes import StrictDB  # noqa: E402
from api.routers import stores  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.stores_util import physical_stores  # noqa: E402

BOKARO = "gid://shopify/Location/58793230523"
PUNE = "gid://shopify/Location/76684427513"
PUNE_UUID = "4dc49c44-1111-2222-3333-444444444444"
_SUPER = {"user_id": "su-1", "roles": ["SUPERADMIN"], "store_ids": []}
_ENTITY = {
    "entity_id": "ent_abc123",
    "gstins": [{"gstin": "20AAPFU0939F1ZV", "state_code": "20", "is_primary": True}],
}


def _store(code, store_id=None, **extra):
    doc = {
        "store_id": store_id or code,
        "store_code": code,
        "store_name": extra.pop("store_name", code),
        "brand": "BETTER_VISION",
        "entity_id": "ent_abc123",
        "is_active": True,
        "store_type": "RETAIL",
    }
    doc.update(extra)
    return doc


class _Repo:
    """The store repository over the SAME StrictDB collection the validator
    reads, so a duplicate check sees what a save wrote."""

    def __init__(self, db):
        self.coll = db.get_collection("stores")

    def find_by_id(self, store_id):
        return self.coll.find_one({"store_id": store_id})

    def find_by_code(self, code):
        return self.coll.find_one({"store_code": code})

    def create(self, data):
        self.coll.insert_one(dict(data))
        return self.coll.find_one({"store_id": data["store_id"]})

    def update(self, store_id, data):
        self.coll.update_one({"store_id": store_id}, {"$set": dict(data)})
        return True


def _world(monkeypatch, docs):
    db = StrictDB()
    db.seed("entities", [_ENTITY])
    db.seed("stores", docs)
    repo = _Repo(db)
    monkeypatch.setattr(stores, "get_store_repository", lambda: repo)
    monkeypatch.setattr(stores, "_get_db", lambda: db)
    # The locations read stays DARK: gate off + an exploding network.
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)

    async def _explode(db, query, variables):  # noqa: ARG001
        raise AssertionError("locations read reached the network while DARK")

    monkeypatch.setattr(shopify_push, "_graphql", _explode)
    app = FastAPI()
    app.include_router(stores.router, prefix="/api/v1/stores")

    async def _u():
        return dict(_SUPER)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app), db


def _saved(db, store_id):
    return db.get_collection("stores").find_one({"store_id": store_id})


# ---------------------------------------------------------------------------
# 1 + 2: persisted through exclude_unset; digits promoted; malformed; clear
# ---------------------------------------------------------------------------


def test_put_persists_location_through_exclude_unset(monkeypatch):
    c, db = _world(monkeypatch, [_store("BV-BOK-02")])
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": BOKARO})
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == BOKARO


def test_bare_digits_are_promoted_to_a_location_gid(monkeypatch):
    c, db = _world(monkeypatch, [_store("BV-BOK-02")])
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": "58793230523"})
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == BOKARO


@pytest.mark.parametrize("bad", ["gid://shopify/Product/1", "sector-4", "gid://shopify/Location/x"])
def test_malformed_location_is_400(monkeypatch, bad):
    c, db = _world(monkeypatch, [_store("BV-BOK-02")])
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": bad})
    assert r.status_code == 400, r.text
    assert "shopify_location_id" in r.json()["detail"]
    assert "shopify_location_id" not in _saved(db, "BV-BOK-02")


def test_empty_string_clears_gid_and_name(monkeypatch):
    c, db = _world(
        monkeypatch,
        [_store("BV-BOK-02", shopify_location_id=BOKARO, shopify_location_name="Better Vision Sector 4")],
    )
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": ""})
    assert r.status_code == 200, r.text
    saved = _saved(db, "BV-BOK-02")
    assert saved["shopify_location_id"] == ""
    assert saved["shopify_location_name"] is None


# ---------------------------------------------------------------------------
# 3: ONLINE stores hold no stock
# ---------------------------------------------------------------------------


def test_known_online_id_refuses_a_location(monkeypatch):
    # No store_type on the doc: the KNOWN-ID branch is what refuses.
    c, db = _world(monkeypatch, [_store("BV-ONLINE-01", store_type=None)])
    r = c.put("/api/v1/stores/BV-ONLINE-01", json={"shopify_location_id": BOKARO})
    assert r.status_code == 400, r.text
    assert "Online stores" in r.json()["detail"]
    assert "shopify_location_id" not in _saved(db, "BV-ONLINE-01")


def test_online_store_type_on_the_doc_refuses_a_location(monkeypatch):
    c, db = _world(monkeypatch, [_store("WO-WEB-09", store_type="ONLINE")])
    r = c.put("/api/v1/stores/WO-WEB-09", json={"shopify_location_id": BOKARO})
    assert r.status_code == 400, r.text
    assert "shopify_location_id" not in _saved(db, "WO-WEB-09")


def test_post_with_online_type_refuses_a_location(monkeypatch):
    c, db = _world(monkeypatch, [])
    body = {
        "store_code": "WO-WEB-09", "store_name": "WizOpt web", "brand": "WIZOPT",
        "entity_id": "ent_abc123", "address": "x", "city": "Pune", "state": "Maharashtra",
        "state_code": "27", "pincode": "411001", "phone": "9876543210",
        "store_type": "ONLINE", "shopify_location_id": BOKARO,
    }
    r = c.post("/api/v1/stores", json=body)
    assert r.status_code == 400, r.text
    assert db.get_collection("stores").find_one({"store_code": "WO-WEB-09"}) is None


def test_clearing_on_an_online_store_is_allowed(monkeypatch):
    c, _ = _world(monkeypatch, [_store("BV-ONLINE-01", store_type="ONLINE")])
    r = c.put("/api/v1/stores/BV-ONLINE-01", json={"shopify_location_id": ""})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 4: one shelf, one location
# ---------------------------------------------------------------------------


def test_duplicate_gid_across_stores_is_409_naming_the_holder(monkeypatch):
    c, db = _world(
        monkeypatch,
        [_store("BV-BOK-02", shopify_location_id=BOKARO), _store("BV-DHN-02")],
    )
    r = c.put("/api/v1/stores/BV-DHN-02", json={"shopify_location_id": "58793230523"})
    assert r.status_code == 409, r.text
    assert "BV-BOK-02" in r.json()["detail"]
    assert "shopify_location_id" not in _saved(db, "BV-DHN-02")


def test_resaving_own_gid_is_not_a_duplicate(monkeypatch):
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": BOKARO, "city": "Bokaro"})
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-BOK-02")["city"] == "Bokaro"


def test_post_create_persists_a_promoted_gid(monkeypatch):
    c, db = _world(monkeypatch, [])
    body = {
        "store_code": "BV-DHN-02", "store_name": "HIRAPUR-DHN", "brand": "BETTER_VISION",
        "entity_id": "ent_abc123", "address": "x", "city": "Dhanbad", "state": "Jharkhand",
        "state_code": "20", "pincode": "826001", "phone": "9876543210",
        "shopify_location_id": "12345",
    }
    r = c.post("/api/v1/stores", json=body)
    assert r.status_code == 201, r.text
    assert _saved(db, "BV-DHN-02")["shopify_location_id"] == "gid://shopify/Location/12345"


# ---------------------------------------------------------------------------
# 5: the display name comes from Shopify's read
# ---------------------------------------------------------------------------


def test_name_is_copied_from_the_locations_read_on_save(monkeypatch):
    c, db = _world(monkeypatch, [_store("BV-BOK-02")])

    async def _read(db):  # noqa: ARG001
        return {"mode": "LIVE", "reason": None,
                "locations": [{"id": BOKARO, "name": "Better Vision Sector 4"}]}

    monkeypatch.setattr(shopify_push, "list_locations", _read)
    r = c.put(
        "/api/v1/stores/BV-BOK-02",
        json={"shopify_location_id": BOKARO, "shopify_location_name": "typed by hand"},
    )
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-BOK-02")["shopify_location_name"] == "Better Vision Sector 4"


def test_name_falls_back_to_the_client_text_when_dark(monkeypatch):
    c, db = _world(monkeypatch, [_store("BV-BOK-02")])
    r = c.put(
        "/api/v1/stores/BV-BOK-02",
        json={"shopify_location_id": BOKARO, "shopify_location_name": "Sector 4"},
    )
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-BOK-02")["shopify_location_name"] == "Sector 4"


# ---------------------------------------------------------------------------
# 6: physical_stores -- the ONE reader
# ---------------------------------------------------------------------------


def _physical_world():
    db = StrictDB()
    db.seed(
        "stores",
        [
            _store("BV-PUN-01", store_id=PUNE_UUID, store_name="GANGADHAM- PUNE", shopify_location_id=PUNE),
            _store("BV-BOK-02", store_name="Sec 4 Bokaro", shopify_location_id=BOKARO,
                   shopify_location_name="Better Vision Sector 4"),
            _store("BV-DHN-02", store_name="HIRAPUR-DHN"),
            _store("BV-HQ-01", store_type="HQ"),
            _store("BV-OLD-01", is_active=False),
            _store("BV-ONLINE-01", store_type=None),  # known id, no store_type
            _store("WO-WEB-09", store_type="ONLINE"),  # new id, typed ONLINE
        ],
    )
    return db


def test_physical_stores_active_and_not_online_sorted_with_uuid_ids():
    rows = physical_stores(_physical_world())
    assert [r["store_code"] for r in rows] == ["BV-BOK-02", "BV-DHN-02", "BV-HQ-01", "BV-PUN-01"]
    by_code = {r["store_code"]: r for r in rows}
    assert by_code["BV-PUN-01"]["store_id"] == PUNE_UUID
    assert by_code["BV-PUN-01"]["shopify_location_id"] == PUNE
    assert by_code["BV-BOK-02"]["shopify_location_name"] == "Better Vision Sector 4"
    assert by_code["BV-DHN-02"].get("shopify_location_id") is None
    # projected: never the whole doc
    assert "brand" not in by_code["BV-BOK-02"] and "_id" not in by_code["BV-BOK-02"]


def test_physical_stores_derives_the_map_and_its_reverse():
    rows = physical_stores(_physical_world())
    gid_by_store = {r["store_id"]: r["shopify_location_id"] for r in rows if r.get("shopify_location_id")}
    assert gid_by_store == {PUNE_UUID: PUNE, "BV-BOK-02": BOKARO}
    assert {v: k for k, v in gid_by_store.items()}[BOKARO] == "BV-BOK-02"


def test_physical_stores_without_a_db_is_empty(monkeypatch):
    from api.services import stores_util

    monkeypatch.setattr(stores_util, "_resolve_db", lambda db: None)
    assert physical_stores(None) == []


# ---------------------------------------------------------------------------
# 7: the migration script goes through the router's validator
# ---------------------------------------------------------------------------


def _script():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts", "migrate_store_locations.py",
    )
    spec = importlib.util.spec_from_file_location("migrate_store_locations", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_set_is_refused_by_the_routers_own_rules(monkeypatch):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    mod = _script()
    db = StrictDB()
    db.seed("stores", [
        _store("BV-BOK-02", shopify_location_id=BOKARO),
        _store("BV-DHN-02"),
        _store("BV-ONLINE-01", store_type="ONLINE"),
    ])
    plan = mod.plan_sets(db, mod.parse_sets([
        "bv-dhn-02=58793230523",      # duplicate of Bokaro's gid -> 409
        "BV-ONLINE-01=1",             # online store -> 400
        "BV-NOPE-01=1",               # unknown code
        "BV-DHN-02=gid://shopify/Product/9",  # malformed -> 400
    ]))
    dup, online, unknown, malformed = plan
    assert dup["code"] == "BV-DHN-02" and dup["error"].startswith("409") and "BV-BOK-02" in dup["error"]
    assert online["error"].startswith("400") and "Online stores" in online["error"]
    assert unknown["error"] == "no store with that store_code"
    assert malformed["error"].startswith("400") and "shopify_location_id" in malformed["error"]


def test_script_clean_plan_promotes_digits_and_writes_through_the_repo(monkeypatch):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    mod = _script()
    db = StrictDB()
    db.seed("stores", [_store("BV-BOK-02"), _store("BV-PUN-01", store_id=PUNE_UUID)])
    db.seed("storefronts", [{"storefront_id": "BV", "online_location_id": BOKARO,
                              "online_location_name": "Better Vision Sector 4",
                              "online_location_resolved_at": "x", "keep": 1}])
    plan = mod.plan_sets(db, mod.parse_sets(["BV-BOK-02=58793230523"]))
    assert [r["error"] for r in plan] == [None]
    assert plan[0]["gid"] == BOKARO and plan[0]["store_id"] == "BV-BOK-02"
    assert mod.apply_sets(db, plan) == 1
    assert db.get_collection("stores").find_one({"store_code": "BV-BOK-02"})["shopify_location_id"] == BOKARO
    # Pune untouched: the script maps only what it is told to
    assert "shopify_location_id" not in db.get_collection("stores").find_one({"store_code": "BV-PUN-01"})
    assert mod.unset_registry(db) == 1
    row = db.get_collection("storefronts").find_one({"storefront_id": "BV"})
    assert row["keep"] == 1 and not any(k in row for k in mod._REGISTRY_KEYS)


def test_script_parse_sets_rejects_a_bare_code():
    mod = _script()
    with pytest.raises(SystemExit):
        mod.parse_sets(["BV-BOK-02"])


def test_script_refuses_pune_by_code_or_by_location_number(monkeypatch):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    mod = _script()
    db = StrictDB()
    db.seed("stores", [_store("BV-PUN-01", store_id=PUNE_UUID), _store("BV-DHN-02")])
    plan = mod.plan_sets(db, mod.parse_sets([
        "BV-PUN-01=76684427513",                          # Pune's code
        "BV-PUN-01=1",                                    # Pune's code with a FOREIGN number: the code branch alone
        "bv-dhn-02=76684427513",                          # Pune's number on another code
        "BV-DHN-02=gid://shopify/Location/76684427513",   # the gid form
    ]))
    assert [r["error"] for r in plan] == [mod.PUNE_REFUSAL] * 4
    assert "49 opening-stock" in mod.PUNE_REFUSAL and "--i-know-pune" in mod.PUNE_REFUSAL
    assert all(r["store_id"] is None for r in plan)
    # a refused row never reaches the repository
    assert mod.apply_sets(db, [r for r in plan if not r["error"]]) == 0
    for code in ("BV-PUN-01", "BV-DHN-02"):
        assert "shopify_location_id" not in db.get_collection("stores").find_one({"store_code": code})


def test_script_i_know_pune_lifts_the_guard_but_not_the_routers_rules(monkeypatch):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    mod = _script()
    db = StrictDB()
    db.seed("stores", [_store("BV-PUN-01", store_id=PUNE_UUID), _store("BV-ONLINE-01", store_type="ONLINE")])
    plan = mod.plan_sets(
        db,
        mod.parse_sets(["BV-PUN-01=76684427513", "BV-ONLINE-01=76684427513"]),
        allow_pune=True,
    )
    assert plan[0]["error"] is None and plan[0]["gid"] == PUNE and plan[0]["store_id"] == PUNE_UUID
    assert plan[1]["error"].startswith("400") and "Online stores" in plan[1]["error"]


# ---------------------------------------------------------------------------
# 8: ONE holder reader -- the validator agrees with the dropdown
# ---------------------------------------------------------------------------


def test_online_doc_carrying_a_gid_is_not_a_holder(monkeypatch):
    # physical_stores (the dropdown join) says PUNE is free; the validator must too.
    c, db = _world(monkeypatch, [
        _store("BV-ONLINE-01", store_type="ONLINE", shopify_location_id=PUNE),
        _store("BV-PUN-01", store_id=PUNE_UUID),
    ])
    r = c.put(f"/api/v1/stores/{PUNE_UUID}", json={"shopify_location_id": PUNE})
    assert r.status_code == 200, r.text
    assert _saved(db, PUNE_UUID)["shopify_location_id"] == PUNE


def test_inactive_doc_carrying_a_gid_is_not_a_holder(monkeypatch):
    c, db = _world(monkeypatch, [
        _store("BV-OLD-01", is_active=False, shopify_location_id=BOKARO),
        _store("BV-BOK-02"),
    ])
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": BOKARO})
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == BOKARO


def test_flipping_a_mapped_store_to_online_is_400_until_cleared(monkeypatch):
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    r = c.put("/api/v1/stores/BV-BOK-02", json={"store_type": "ONLINE"})
    assert r.status_code == 400, r.text
    assert "Shopify location" in r.json()["detail"]
    assert _saved(db, "BV-BOK-02")["store_type"] == "RETAIL"
    # clearing the mapping in the same write is the way through
    r = c.put("/api/v1/stores/BV-BOK-02", json={"store_type": "ONLINE", "shopify_location_id": ""})
    assert r.status_code == 200, r.text
    saved = _saved(db, "BV-BOK-02")
    assert saved["store_type"] == "ONLINE" and saved["shopify_location_id"] == ""


def test_reactivating_a_doc_whose_gid_an_active_shop_holds_is_409(monkeypatch):
    c, db = _world(monkeypatch, [
        _store("BV-OLD-01", is_active=False, shopify_location_id=BOKARO),
        _store("BV-BOK-02", shopify_location_id=BOKARO),
    ])
    r = c.put("/api/v1/stores/BV-OLD-01", json={"is_active": True})
    assert r.status_code == 409, r.text
    assert "BV-BOK-02" in r.json()["detail"]
    assert _saved(db, "BV-OLD-01")["is_active"] is False
    # an unrelated edit on the inactive doc is not blocked
    r = c.put("/api/v1/stores/BV-OLD-01", json={"city": "Ranchi"})
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-OLD-01")["city"] == "Ranchi"
