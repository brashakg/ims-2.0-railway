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
     location number) is refused at plan time unless --i-know-pune; two --set
     rows naming the same location in one run are refused (the router's
     check reads DB state neither row has written yet); an identical
     re-apply is skipped and counted, not re-stamped by the REAL repository;
     a refused write exits 1 before the registry $unset.
  8. ONE holder reader: the validator's "who holds this gid" is
     stores_util.physical_stores -- the same list the dropdown joins against
     -- so an ONLINE or inactive doc carrying a gid blocks nobody; flipping a
     mapped shop to ONLINE is 400 until the mapping is cleared; reactivating
     a doc whose gid an active shop now holds is 409 (an unrelated edit on
     the inactive doc is not).
  9. A shop that still holds stock the WEBSITE LISTS does not lose its
     correction (round 4): clearing or changing its location RELEASES the old
     one first -- zero at the old gid through THE writer, then the shop's rows
     dropped from the last-sent baseline (which is keyed by store, not by
     location), and only then the save. Shopify refusing the release refuses
     the save. A first mapping releases nothing (the go-live step), stock on
     no listing releases nothing (it leaves no phantom), and the migration
     script -- which cannot run the async release -- refuses instead.

 10. Panel round 5: the baseline FORGET runs on EVERY mapping change and
     covers EVERY listing (gated on the shelf, a re-map of a shop holding
     nothing was a green NOOP that never wrote the new location; scoped to the
     held SKUs, it re-armed ~1 of 121 listings). ONE spelling of "which listed
     units does this shop hold" (release_store_location), so an unreadable
     shelf is still a 503 and a padded `products.sku` no longer waves a remap
     through; an unresolved collection RAISES. The gid is normalised in the ONE
     store reader, so a bare-digit doc is one location to the writer, the 409
     and the dropdown alike.

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

from strict_fakes import StrictCollection, StrictDB  # noqa: E402
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
    assert mod.apply_sets(db, plan) == {"written": 1, "identical": 0, "failed": 0}
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


def test_R3_the_script_maps_pune_like_any_other_shop(monkeypatch):
    """Round-3 ops P1. The script refused any --set naming BV-PUN-01 or
    location 76684427513 because Shopify held 49 units at Gangadham Pune and
    the IMS ledger held 1. On 2026-09-07 the owner deleted the entire
    catalogue -- 43 Shopify products, and IMS products / catalog_products /
    catalog_variants / stock_units -- so the 49 no longer exist and the
    precondition the refusal demanded ("re-run once the ledger shows them")
    could never be met: the operator's only routes were to defeat the guard by
    asserting something untrue, or to skip the script for the Organization
    dropdown, which carries no such guard at all. Restore PUNE_REFUSAL ->
    plan_sets returns an error row -> this fails."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    mod = _script()
    assert not hasattr(mod, "PUNE_REFUSAL") and not hasattr(mod, "pune_guarded")
    db = StrictDB()
    db.seed("stores", [_store("BV-PUN-01", store_id=PUNE_UUID), _store("BV-DHN-02")])
    plan = mod.plan_sets(db, mod.parse_sets(["BV-PUN-01=76684427513", "BV-DHN-02=58793230523"]))
    assert [r["error"] for r in plan] == [None, None]
    assert plan[0]["store_id"] == PUNE_UUID and plan[0]["gid"] == PUNE
    assert mod.apply_sets(db, plan) == {"written": 2, "identical": 0, "failed": 0}
    assert _saved(db, PUNE_UUID)["shopify_location_id"] == PUNE
    # ...and the router's own rules still bite: a second shop cannot claim it.
    with pytest.raises(stores.HTTPException) as exc:
        stores._validate_store_payload({"shopify_location_id": PUNE}, db=db, store_id="BV-DHN-02",
                                       existing=_saved(db, "BV-DHN-02"))
    assert exc.value.status_code == 409


def test_script_refuses_a_duplicate_gid_within_one_run(monkeypatch):
    """Two --set rows naming the SAME location in one run: the router's
    duplicate check reads DB state, which neither row has written yet, so
    both would pass it and apply would put one shelf on two stores. The plan
    refuses the second row (normalised compare: bare digits vs gid form)."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    mod = _script()
    db = StrictDB()
    db.seed("stores", [_store("BV-BOK-02"), _store("BV-DHN-02"), _store("WIZ-DHN-01")])
    plan = mod.plan_sets(db, mod.parse_sets([
        "BV-BOK-02=58793230523",
        "BV-DHN-02=gid://shopify/Location/58793230523",   # same shelf, gid form
        "WIZ-DHN-01=1",                                    # unrelated, stays clean
    ]))
    first, dup, other = plan
    assert first["error"] is None and first["gid"] == BOKARO
    assert dup["error"].startswith("409") and "duplicate within this run" in dup["error"]
    assert "BV-BOK-02" in dup["error"]
    assert other["error"] is None
    # a refused row never reaches the repository; the clean rows write one shelf each
    assert mod.apply_sets(db, [r for r in plan if not r["error"]]) == {"written": 2, "identical": 0, "failed": 0}
    holders = [d["store_code"] for d in db.get_collection("stores").find({"shopify_location_id": BOKARO})]
    assert holders == ["BV-BOK-02"]


def test_script_identical_re_apply_is_skipped_not_rewritten(monkeypatch):
    """The REAL StoreRepository stamps updated_at into every $set, so its
    modified_count reports True on an idempotent re-apply. The plan compares
    the doc first: an identical gid + name is skipped, counted 'identical',
    and the doc (updated_at included) is untouched. With the gates dark the
    name read answers None; a doc whose gid is unchanged keeps the name it
    already shows instead of having it blanked."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    mod = _script()
    db = StrictDB()
    db.seed("stores", [
        _store("BV-BOK-02"),
        _store("BV-DHN-02", shopify_location_id="gid://shopify/Location/77", shopify_location_name="HIRAPUR-DHN"),
    ])
    db.seed("storefronts", [{"storefront_id": "BV", "online_location_id": BOKARO}])
    sets = mod.parse_sets(["BV-BOK-02=58793230523", "BV-DHN-02=77"])
    plan = mod.plan_sets(db, sets)
    assert [r["same"] for r in plan] == [False, True]
    assert plan[1]["name"] == "HIRAPUR-DHN"  # kept from the doc, not blanked by the dark read
    assert mod.apply_plan(db, plan) == {"written": 1, "identical": 1, "failed": 0, "unset": 1}
    stores = db.get_collection("stores")
    bok_after_first = dict(stores.find_one({"store_code": "BV-BOK-02"}))
    assert bok_after_first["shopify_location_id"] == BOKARO and "updated_at" in bok_after_first
    dhn = dict(stores.find_one({"store_code": "BV-DHN-02"}))
    assert dhn["shopify_location_name"] == "HIRAPUR-DHN" and "updated_at" not in dhn

    plan2 = mod.plan_sets(db, sets)
    assert [r["same"] for r in plan2] == [True, True]
    second = mod.apply_plan(db, plan2)
    # `unset` is not pinned on the re-run: StrictDB's update_one reports
    # modified_count 1 for ANY matched doc (real Mongo says 0 for a no-op $unset).
    assert {k: second[k] for k in ("written", "identical", "failed")} == {"written": 0, "identical": 2, "failed": 0}
    assert dict(stores.find_one({"store_code": "BV-BOK-02"})) == bok_after_first
    row = db.get_collection("storefronts").find_one({"storefront_id": "BV"})
    assert not any(k in row for k in mod._REGISTRY_KEYS)


def test_script_failed_write_stops_before_the_registry_unset(monkeypatch):
    """A mapping write the repository refused (it swallows the Mongo error and
    returns False) exits 1 BEFORE the registry $unset, so the #1125 pooled
    row is never removed while a store is left unmapped."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    mod = _script()
    monkeypatch.setattr(mod.StoreRepository, "update", lambda self, *_a, **_k: False)
    db = StrictDB()
    db.seed("stores", [_store("BV-BOK-02")])
    db.seed("storefronts", [{"storefront_id": "BV", "online_location_id": BOKARO}])
    plan = mod.plan_sets(db, mod.parse_sets(["BV-BOK-02=58793230523"]))
    assert plan[0]["error"] is None and not plan[0]["same"]
    with pytest.raises(SystemExit) as exc:
        mod.apply_plan(db, plan)
    assert exc.value.code == 1
    assert db.get_collection("storefronts").find_one({"storefront_id": "BV"})["online_location_id"] == BOKARO


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


def test_R5_a_bare_digit_gid_on_a_doc_is_one_location_to_every_reader(monkeypatch):
    """ROUND-5 P3 (latent, one rule / two spellings). The WRITER normalised
    every store gid through `_as_shopify_gid` while the 409 duplicate refusal
    (`_location_holder`) and the dropdown's `by_gid` compared the doc's RAW
    string against an already-normalised gid. A doc carrying bare digits was
    therefore MAPPED to the writer and UNMAPPED to both of those: two shops
    could save one location, and `_mapped` then wrote NEITHER of them
    (STORE_LOCATION_DUPLICATE) -- both shops' stock invisible online. Fixed in
    the ONE reader (stores_util.physical_stores), so every comprehension over
    it agrees. Revert the normalising loop there -> no 409 -> this fails."""
    c, db = _world(monkeypatch, [
        _store("BV-BOK-02", shopify_location_id="58793230523"),  # a direct DB write
        _store("BV-DHN-02"),
    ])
    rows = {r["store_code"]: r for r in physical_stores(db)}
    assert rows["BV-BOK-02"]["shopify_location_id"] == BOKARO, "one spelling for every reader"
    assert shopify_push.mapped_store_locations(physical_stores(db)) == {"BV-BOK-02": BOKARO}
    r = c.put("/api/v1/stores/BV-DHN-02", json={"shopify_location_id": BOKARO})
    assert r.status_code == 409, r.text
    assert "BV-BOK-02" in r.json()["detail"]
    r = c.put("/api/v1/stores/BV-DHN-02", json={"shopify_location_id": "58793230523"})
    assert r.status_code == 409, r.text


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


# ---------------------------------------------------------------------------
# 9: a shop holding units keeps its location (phantom stock guard)
# ---------------------------------------------------------------------------


def _unit(store_id, status="AVAILABLE"):
    return {"stock_id": f"u-{store_id}-{status}", "product_id": "p1", "store_id": store_id, "status": status}


def _list_it(db, pid="p1", sku="SP-1"):
    """Put ``pid``'s product ON THE WEBSITE. Only listed stock can leave a
    phantom at the old Shopify location, so only listed stock blocks a remap."""
    db.seed("products", [{"product_id": pid, "sku": sku}])
    db.seed(
        "catalog_products",
        [{"id": "cat-1", "sku": sku, "ecom": {"shopify_inventory_item_id": "gid://shopify/InventoryItem/9"}}],
    )
    return db


def _go_live(monkeypatch, responses):
    """Flip the DARK world _world builds to LIVE with a canned Shopify. Returns
    the transcript list (one entry per GraphQL call)."""
    calls = []

    async def _graphql(db, query, variables):  # noqa: ARG001
        calls.append({"query": query, "variables": variables})
        for marker, body in responses.items():
            if marker in query:
                return body
        return {"data": {}}

    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    monkeypatch.setattr(shopify_push, "_graphql", _graphql)
    return calls


_SET_OK = {
    "inventorySetQuantities": {
        "data": {
            "inventorySetQuantities": {
                "inventoryAdjustmentGroup": {"createdAt": "now"},
                "userErrors": [],
            }
        }
    },
    "locations(": {"data": {"locations": {"nodes": []}}},
}
_SET_REFUSED = {
    "inventorySetQuantities": {
        "data": {
            "inventorySetQuantities": {
                "inventoryAdjustmentGroup": None,
                "userErrors": [{"field": ["input"], "message": "Location does not exist", "code": "INVALID"}],
            }
        }
    },
    "locations(": {"data": {"locations": {"nodes": []}}},
}


def test_R4_changing_the_location_releases_the_old_one_instead_of_refusing(monkeypatch):
    """ROUND-4 P3 (ops trap, exactly the fresh-setup window). Any CHANGE or
    CLEAR of shopify_location_id used to be a flat 400 while the shop held
    stock the website lists -- "Transfer the units out first" -- with no
    supported "zero the old location, then remap" path anywhere. On first setup
    the owner picks one of four similarly-named locations from a dropdown that
    pre-selects only on an EXACT name match; the first wrong pick then became
    PERMANENT the moment the shop took one GRN unit of a listed product, and
    Shopify kept showing Bokaro's numbers at the Pune location.

    The remedy is the release, not the refusal: the door now zeroes the shop's
    listed units AT THE OLD LOCATION through THE writer, forgets that shop's
    baseline (which is keyed by STORE, not location, so the NEW location would
    otherwise never be written at all), and only then saves.

    Revert to `raise HTTPException(400, ...)` -> the PUT is refused -> fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO), _store("BV-DHN-02")])
    db.seed("stock_units", [_unit("BV-BOK-02")])
    _list_it(db)
    # the baseline the old location was last written with
    db.get_collection("catalog_products").update_one(
        {"id": "cat-1"},
        {"$set": {"ecom.online_stock": {"tracked": True, "quantities": {"SP-1": {"BV-BOK-02": 1, "BV-DHN-02": 4}}}}},
    )
    calls = _go_live(monkeypatch, _SET_OK)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == PUNE
    sets = [c_ for c_ in calls if "inventorySetQuantities" in c_["query"]]
    assert len(sets) == 1
    assert sets[0]["variables"]["input"]["quantities"] == [
        {"inventoryItemId": "gid://shopify/InventoryItem/9", "locationId": BOKARO, "quantity": 0}
    ], "the OLD location is zeroed, so it stops advertising units nobody will write"
    stock = db.get_collection("catalog_products").find_one({"id": "cat-1"})["ecom"]["online_stock"]
    assert stock["quantities"] == {"SP-1": {"BV-DHN-02": 4}}, (
        "the moved shop is dropped from the baseline so the NEW location is written next pass"
    )


def test_R4_a_remap_is_refused_when_shopify_refuses_the_release(monkeypatch):
    """The release is the precondition, not a side effect: if Shopify refuses
    the zeroing, the mapping must stay as it was rather than stranding the
    numbers on a location nothing writes again. Revert the `if not
    released.get("ok")` branch -> 200 with the old location live -> fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [_unit("BV-BOK-02")])
    _list_it(db)
    _go_live(monkeypatch, _SET_REFUSED)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 400, r.text
    assert "keep showing on the website" in r.json()["detail"]
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == BOKARO


def test_R4_the_migration_script_still_refuses_an_unreleased_remap(monkeypatch):
    """scripts/migrate_store_locations.py --set writes straight to the store
    repository, so it cannot run the async release the PUT door runs. It must
    REFUSE (409) rather than remap and strand the numbers. Delete the
    `if release_old:` branch in the script -> a clean plan -> fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [_unit("BV-BOK-02")])
    _list_it(db)
    plan = _script().plan_sets(db, [("BV-BOK-02", PUNE)])
    assert plan[0]["error"].startswith("409:")
    assert "Organization page" in plan[0]["error"]


def test_R4_a_first_mapping_never_releases_anything(monkeypatch):
    """A shop with NO location yet is the go-live step, not a correction: it
    holds listed units and still saves with zero Shopify calls."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02")])
    db.seed("stock_units", [_unit("BV-BOK-02")])
    _list_it(db)
    calls = _go_live(monkeypatch, _SET_OK)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": BOKARO})
    assert r.status_code == 200, r.text
    assert [c_ for c_ in calls if "inventorySetQuantities" in c_["query"]] == []


def test_R4_stock_that_is_on_no_listing_still_never_touches_shopify(monkeypatch):
    """Round-3 ops P4, still true: a product on NO listing shows nowhere, so
    remapping a shop that only holds unlisted stock writes NOTHING to Shopify
    (the baseline is still re-armed -- see the round-5 tests below). Widen the
    zeroing in `release_store_location` from `listed_skus_on_hand_at` to every
    on-hand unit -> a pointless inventorySetQuantities -> this fails."""
    c, db = _world(monkeypatch, [_store("BV-DHN-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [_unit("BV-DHN-02")])
    db.seed("products", [{"product_id": "p1", "sku": "SP-1"}])  # never pushed: no ecom row
    calls = _go_live(monkeypatch, _SET_OK)
    r = c.put("/api/v1/stores/BV-DHN-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-DHN-02")["shopify_location_id"] == PUNE
    assert [c_ for c_ in calls if "inventorySetQuantities" in c_["query"]] == []


def test_R3_P5_a_remap_is_refused_when_the_shelf_cannot_be_read(monkeypatch):
    """Round-3 P5: the ONE door that reopened the remap phantom. The on-hand
    count was wrapped in `except: pass` -> None, which every caller reads as
    "the shop holds nothing", so a Mongo blip waved the remap through and the
    old location advertised its units forever. stores_util propagates a Mongo
    error for exactly this reason. Revert to `except: pass` -> 200 -> fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [_unit("BV-BOK-02"), _unit("BV-BOK-02", status="RESERVED")])
    _list_it(db)

    class _Dead(type(db.get_collection("stock_units"))):
        def count_documents(self, *a, **k):
            raise RuntimeError("stock read died")

        def distinct(self, *a, **k):
            raise RuntimeError("stock read died")

    db._collections["stock_units"] = _Dead("stock_units", [])
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 503, r.text
    assert "Cannot read this shop" in r.json()["detail"]
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == BOKARO
    # The deactivation door reads the same shelf and refuses the same way.
    r = c.put("/api/v1/stores/BV-BOK-02", json={"is_active": False})
    assert r.status_code == 503, r.text


def test_R3_a_dead_catalog_never_reads_as_nothing_is_listed(monkeypatch):
    """inventory_items_for_skus is fail-SOFT ({} on a bad read) and {} means
    "nothing is listed" -- which would wave the remap through. Revert the
    strict `catalog.find_one({}, ...)` touch -> 200 -> fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [_unit("BV-BOK-02")])
    _list_it(db)

    class _DeadCatalog(type(db.get_collection("catalog_products"))):
        def find_one(self, *a, **k):
            raise RuntimeError("catalog read died")

        def find(self, *a, **k):
            raise RuntimeError("catalog read died")

    db._collections["catalog_products"] = _DeadCatalog("catalog_products", [])
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 503, r.text
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == BOKARO


def test_R3_on_hand_here_is_item_events_on_hand_match(monkeypatch):
    """One spelling of on-hand. The hand-typed
    {"status": {"$nin": ["SOLD", "RETURNED", "SCRAPPED"]}} counted a VOID /
    DAMAGED / RTV unit as on hand (none of those is in the $nin set, and two of
    its three tokens are not even StockState members). Revert to the $nin
    query -> the VOID unit blocks -> this fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [_unit("BV-BOK-02", status="VOID")])
    _list_it(db)
    assert stores._store_on_hand_units(db, "BV-BOK-02") is None
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 200, r.text


def test_location_can_be_cleared_or_changed_once_the_units_have_left(monkeypatch):
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [_unit("BV-BOK-02", status="SOLD")])  # gone from the shelf
    _list_it(db)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == PUNE
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": ""})
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == ""


# ---------------------------------------------------------------------------
# Panel round 5: the FORGET is unconditional, and covers every listing
# ---------------------------------------------------------------------------


def _baseline_of(db, pid="cat-1"):
    return db.get_collection("catalog_products").find_one({"id": pid})["ecom"]["online_stock"]["quantities"]


def test_R5_a_remap_of_a_shop_holding_nothing_still_re_arms_the_baseline(monkeypatch):
    """ROUND-5 P1 (HIGH, oversell). The forget -- the ONLY thing that lets the
    per-store diff see a mapping change -- sat behind "does this shop hold
    listed units". A shop that holds NONE (every Jharkhand shop the owner
    mapped on 09-06 while the catalogue was empty) therefore saved its new gid
    with no release and no forget: the baseline still carried that store's row
    on every listing, the next sweep matched it, reported action=noop / ok=True
    / changed=0, and wrote the NEW Shopify location NOTHING -- it kept whatever
    quantity Shopify held there, forever, with no guard naming it (the new
    location IS mapped, so it is no stray; the shop reads 0 units, so it is no
    unmapped holder).

    Restore the `if _store_listed_on_hand_units(...)` gate around release_old,
    or the `if not skus: return out` early exit in release_store_location ->
    the baseline keeps BV-BOK-02 -> this fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    _list_it(db)  # a listing, but the shop holds NO unit of it
    db.get_collection("catalog_products").update_one(
        {"id": "cat-1"},
        {"$set": {"ecom.online_stock": {"tracked": True, "quantities": {"SP-1": {"BV-BOK-02": 0}}}}},
    )
    calls = _go_live(monkeypatch, _SET_OK)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == PUNE
    # Nothing to zero (no listed unit on that shelf) -- and nothing was written.
    assert [c_ for c_ in calls if "inventorySetQuantities" in c_["query"]] == []
    assert _baseline_of(db) == {"SP-1": {}}, (
        "the shop must be dropped from the baseline so the NEW location is written next pass"
    )


def test_R5_the_forget_covers_every_listing_not_just_the_skus_on_the_shelf(monkeypatch):
    """ROUND-5 P2 (HIGH, oversell). The forget was scoped to the SKUs the shop
    happens to hold, but the baseline holds a row for EVERY listed SKU at that
    shop (an explicit 0 included). So a supported remap re-armed the one
    listing on the shelf and left every other listing matching -> those
    listings noop'd and the NEW location received no row for them at all. At
    the production shape (121 listings rebuilt, one shop holding one unit) that
    is ~1 listing re-armed and ~120 left advertising the old location.

    Restore the `skus` argument of `_forget_store_baseline` -> cat-2 keeps
    BV-BOK-02 -> this fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO), _store("BV-DHN-02")])
    db.seed("stock_units", [_unit("BV-BOK-02")])  # one unit of SP-1 only
    _list_it(db)
    db.get_collection("catalog_products").insert_one(
        {"id": "cat-2", "sku": "SP-2",
         "ecom": {"shopify_inventory_item_id": "gid://shopify/InventoryItem/10",
                  "online_stock": {"tracked": True,
                                   "quantities": {"SP-2": {"BV-BOK-02": 0, "BV-DHN-02": 1}}}}}
    )
    db.get_collection("catalog_products").update_one(
        {"id": "cat-1"},
        {"$set": {"ecom.online_stock": {"tracked": True, "quantities": {"SP-1": {"BV-BOK-02": 1}}}}},
    )
    _go_live(monkeypatch, _SET_OK)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 200, r.text
    assert _baseline_of(db, "cat-1") == {"SP-1": {}}
    assert _baseline_of(db, "cat-2") == {"SP-2": {"BV-DHN-02": 1}}, (
        "every listing that carried this shop is re-armed, not only the ones it stocks"
    )


def test_R5_an_unreadable_shelf_still_refuses_the_remap_through_the_one_reader(monkeypatch):
    """The strict read moved into release_store_location (ONE spelling of
    "which listed units does this shop hold" -- the router's second spelling
    re-resolved sku -> product_id through `products` and had two silent
    "holds nothing" exits). It must still be a 503 that leaves the mapping
    alone. Swallow the exception in release_store_location -> 200 -> fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [_unit("BV-BOK-02")])
    _list_it(db)

    class _Dead(type(db.get_collection("stock_units"))):
        def distinct(self, *a, **k):
            raise RuntimeError("stock read died")

    db._collections["stock_units"] = _Dead("stock_units", [])
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 503, r.text
    assert "Cannot read this shop" in r.json()["detail"]
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == BOKARO


def test_R5_a_whitespace_sku_no_longer_waves_a_remap_through(monkeypatch):
    """The deleted second spelling queried `products` with the NORMALISED sku
    while the row stored it with whitespace, so `listed_pids` came back empty
    and the door answered "holds nothing": the remap saved with no release at
    all and the old location kept advertising the unit. One spelling now --
    `listed_skus_on_hand_at` resolves product -> sku, never back again.
    Re-introduce the sku -> product_id round trip -> no release call -> fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [_unit("BV-BOK-02")])
    db.seed("products", [{"product_id": "p1", "sku": " SP-1 "}])  # legacy/imported row
    db.seed("catalog_products", [
        {"id": "cat-1", "sku": "SP-1",
         "ecom": {"shopify_inventory_item_id": "gid://shopify/InventoryItem/9"}}
    ])
    calls = _go_live(monkeypatch, _SET_OK)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 200, r.text
    sets = [c_ for c_ in calls if "inventorySetQuantities" in c_["query"]]
    assert len(sets) == 1 and sets[0]["variables"]["input"]["quantities"] == [
        {"inventoryItemId": "gid://shopify/InventoryItem/9", "locationId": BOKARO, "quantity": 0}
    ], "the OLD location is zeroed even though the products row carries a padded sku"


def test_R5_a_missing_collection_is_unknown_not_nothing(monkeypatch):
    """`listed_skus_on_hand_at` is documented STRICT ("it RAISES on a read it
    cannot answer") and answered [] when any of its three collections did not
    resolve -- the exact exit its own docstring names as the failure. Revert to
    `return []` -> no raise -> this fails."""
    from api.services.online_catalog import listed_skus_on_hand_at

    class _NoCollections:
        """A handle that answers "that collection does not exist" -- what a
        half-provisioned database looks like to `_coll`."""

        def get_collection(self, name):  # noqa: ARG002
            return None

        def __getitem__(self, name):  # noqa: ARG002
            raise KeyError(name)

    with pytest.raises(RuntimeError):
        listed_skus_on_hand_at(_NoCollections(), "BV-BOK-02")


def test_a_first_mapping_is_allowed_while_the_shop_holds_units(monkeypatch):
    """Mapping a shop that already holds stock IS the go-live step."""
    c, db = _world(monkeypatch, [_store("BV-DHN-02")])
    db.seed("stock_units", [_unit("BV-DHN-02")])
    r = c.put("/api/v1/stores/BV-DHN-02", json={"shopify_location_id": BOKARO})
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-DHN-02")["shopify_location_id"] == BOKARO


# ---------------------------------------------------------------------------
# 11. Panel round 6 (2026-09-12): the release door reads what SHOPIFY IS
#     SHOWING (not the shelf), refuses while Shopify is unreachable, and runs
#     on DEACTIVATION and DELETE too -- the two doors that had no release at
#     all.
# ---------------------------------------------------------------------------


def _showing(db, per_store, pid="cat-1", sku="SP-1"):
    """The last-sent baseline: what Shopify is ADVERTISING at each shop right
    now. Written by `_writeback_stock` after every accepted push."""
    db.get_collection("catalog_products").update_one(
        {"id": pid},
        {"$set": {"ecom.online_stock": {"tracked": True, "quantities": {sku: dict(per_store)}}}},
    )


def test_R6_the_release_zeroes_what_shopify_is_showing_not_just_the_shelf(monkeypatch):
    """ROUND-6 oversell P1 (phantom stock at a released location). The release
    decided what to zero from the SHELF (`listed_skus_on_hand_at`), but the
    record of what Shopify is SHOWING is the baseline. `push_skus_stock` is
    fail-soft by design, so the two come apart exactly when it matters: three
    units go SOLD and the post-sale write-back does not land (Shopify refused
    the chunk, or the token lapsed for ten minutes). The shelf now reads 0, the
    baseline still says 3, and 3 is what bettervision.in is selling.

    On the shelf alone the remap wrote NOTHING, forgot the baseline and moved
    the gid away, so the old location was in no store's map and `_mapped` never
    targeted it again: three phantom units for ever, with the stray-location
    guard merely REPORTING it at the next 01:00 sweep.

    Narrow the union back to `listed_skus_on_hand_at` -> zero Shopify calls ->
    this fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [])  # all three sold; the shelf is empty
    _list_it(db)
    _showing(db, {"BV-BOK-02": 3})
    calls = _go_live(monkeypatch, _SET_OK)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 200, r.text
    sets = [x for x in calls if "inventorySetQuantities" in x["query"]]
    assert len(sets) == 1
    assert sets[0]["variables"]["input"]["quantities"] == [
        {"inventoryItemId": "gid://shopify/InventoryItem/9", "locationId": BOKARO, "quantity": 0}
    ], "what Shopify is SHOWING comes down, whatever the shelf says"
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == PUNE
    assert _baseline_of(db) == {"SP-1": {}}


def test_R6_a_dark_remap_is_refused_while_shopify_is_still_showing_units(monkeypatch):
    """ROUND-6 oversell P2. `release_store_location` returned ok with zero
    network when DARK, on the justification that "a dark system never published
    a quantity" -- false for a system that was LIVE yesterday and is dark right
    now. `_has_shopify_creds` is itself fail-soft to False on any vault or
    credential read error, so a ten-minute blip saved the new mapping, forgot
    the baseline, and left the old location selling a unit nothing would ever
    write again.

    DARK with a live baseline behind it is an UNREACHABLE Shopify, not "nothing
    to release". Delete the `if published:` refusal -> 200 with the gid moved
    -> this fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [_unit("BV-BOK-02")])
    _list_it(db)
    _showing(db, {"BV-BOK-02": 1})
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 400, r.text
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == BOKARO
    assert _baseline_of(db) == {"SP-1": {"BV-BOK-02": 1}}, (
        "the record of what is live survives a refusal -- forgetting it is how the "
        "next pass stops re-sending that shop"
    )


def test_R6_a_dark_remap_with_nothing_published_still_saves(monkeypatch):
    """The ceiling on that refusal: a shop that has NEVER been published from
    (no baseline row) has nothing to retract, so the go-live remap still saves
    while dark -- even holding listed units. Widen the refusal from the
    baseline to the shelf -> 400 -> this fails, and first setup is blocked
    until the storefront is armed."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [_unit("BV-BOK-02")])
    _list_it(db)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 200, r.text
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == PUNE


def test_R6_deactivating_a_mapped_shop_releases_its_shopify_location(monkeypatch):
    """ROUND-6 oversell P3. `update_store` with is_active=False ran the
    dependents guard and saved -- it never called `release_store_location`. And
    the dependents guard measures the SHELF, the same wrong source as P1: three
    units sold with the write-back never landing left the shelf empty, the
    guard happy, and 3 on the website. The save then dropped the shop out of
    `physical_stores`, so its gid left the store map and `_mapped` never
    targeted that location again.

    Delete the `_release_location_or_refuse` call in the deactivation branch ->
    zero Shopify calls -> this fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [])
    _list_it(db)
    _showing(db, {"BV-BOK-02": 3})
    calls = _go_live(monkeypatch, _SET_OK)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"is_active": False})
    assert r.status_code == 200, r.text
    sets = [x for x in calls if "inventorySetQuantities" in x["query"]]
    assert len(sets) == 1
    assert sets[0]["variables"]["input"]["quantities"] == [
        {"inventoryItemId": "gid://shopify/InventoryItem/9", "locationId": BOKARO, "quantity": 0}
    ]
    assert _saved(db, "BV-BOK-02")["is_active"] is False
    assert _baseline_of(db) == {"SP-1": {}}


def test_R6_deleting_a_mapped_shop_releases_its_shopify_location(monkeypatch):
    """The same hole in the DELETE door (a soft delete is the same
    deactivation). Delete the `_release_location_or_refuse` call in
    `delete_store` -> zero Shopify calls -> this fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [])
    _list_it(db)
    _showing(db, {"BV-BOK-02": 2})
    calls = _go_live(monkeypatch, _SET_OK)
    r = c.delete("/api/v1/stores/BV-BOK-02")
    assert r.status_code == 200, r.text
    sets = [x for x in calls if "inventorySetQuantities" in x["query"]]
    assert len(sets) == 1
    assert sets[0]["variables"]["input"]["quantities"] == [
        {"inventoryItemId": "gid://shopify/InventoryItem/9", "locationId": BOKARO, "quantity": 0}
    ]
    assert _saved(db, "BV-BOK-02")["is_active"] is False


def test_R6_a_deactivation_is_refused_when_shopify_refuses_the_release(monkeypatch):
    """The release is the precondition for the deactivation too, exactly as it
    is for the remap: Shopify refusing the zeroing leaves the shop ACTIVE
    rather than stranding its numbers on a location nothing writes again."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [])
    _list_it(db)
    _showing(db, {"BV-BOK-02": 3})
    _go_live(monkeypatch, _SET_REFUSED)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"is_active": False})
    assert r.status_code == 400, r.text
    assert "keep showing on the website" in r.json()["detail"]
    assert _saved(db, "BV-BOK-02")["is_active"] is True


def test_R6_deactivating_an_unmapped_shop_touches_nothing(monkeypatch):
    """The ceiling: a shop with no Shopify location has nothing to release, so
    the ordinary deactivation is still one save and zero network."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02")])
    db.seed("stock_units", [])
    _list_it(db)
    calls = _go_live(monkeypatch, _SET_OK)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"is_active": False})
    assert r.status_code == 200, r.text
    assert [x for x in calls if "inventorySetQuantities" in x["query"]] == []


def test_R6_a_sku_the_location_advertises_with_no_shopify_item_refuses_the_release(monkeypatch):
    """The other half of round-6 oversell P1. The union NAMES what Shopify is
    advertising, but the retraction still had to resolve each SKU to a Shopify
    inventory item -- and `inventory_items_for_skus` is fail-SOFT ({} on a bad
    read). {} there reads as "nothing to zero": the door wrote no row, forgot
    the baseline and let the gid walk away, which is the ORIGINAL phantom bug
    rebuilt one layer down, on the very path that closed it.

    Input: the baseline says BV-BOK-02 is showing 3 of a SKU that resolves to
    no inventory item (a size retired off the parent after it was published, or
    the same transient catalog read that makes the resolver answer {}). Nothing
    can retract that number, so the save is refused BEFORE a single row goes
    out -- the shop keeps its location, so the 01:00 sweep still writes it --
    instead of half-releasing and then forgetting.

    Drop the `unreachable` refusal -> 200, zero Shopify calls, gid at Pune and
    the baseline forgotten -> this fails."""
    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [])  # the shelf is empty; only the baseline knows
    _list_it(db)
    _showing(db, {"BV-BOK-02": 3}, sku="GONE-1")
    calls = _go_live(monkeypatch, _SET_OK)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 400, r.text
    assert "GONE-1" in r.json()["detail"]
    assert [x for x in calls if "inventorySetQuantities" in x["query"]] == [], (
        "nothing goes out at all -- half-released then forgotten is the worst outcome"
    )
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == BOKARO
    assert _baseline_of(db) == {"GONE-1": {"BV-BOK-02": 3}}, "the record of what is live survives"


def test_R6_a_baseline_reset_that_dies_refuses_the_save(monkeypatch):
    """The last swallowed exception on this door. The zeroing lands on Shopify
    and THEN `_forget_store_baseline` -- fail-soft, logged at warning -- dies on
    a Mongo blip. The door still answered ok, so the save moved the gid while
    every listing whose baseline still carried this shop went on matching the
    diff, nooping, and never writing the NEW location: round-5 P2 re-entered
    through an `except Exception: pass`.

    The reset is STRICT now and the door turns the raise into a refusal (the
    whole door is idempotent -- a retry writes 0 again). Restore the
    `except Exception: logger.warning(...)` inside `_forget_store_baseline`, or
    drop `_rearm_or_refuse`'s `out["ok"] = False` -> 200 with the gid at Pune
    -> this fails."""

    class _WriteDies(StrictCollection):
        def update_one(self, *a, **k):
            raise RuntimeError("catalog_products write died")

    c, db = _world(monkeypatch, [_store("BV-BOK-02", shopify_location_id=BOKARO)])
    db.seed("stock_units", [_unit("BV-BOK-02")])
    _list_it(db)
    _showing(db, {"BV-BOK-02": 1})
    live = db.get_collection("catalog_products")
    db._collections["catalog_products"] = _WriteDies("catalog_products", live.docs)
    calls = _go_live(monkeypatch, _SET_OK)
    r = c.put("/api/v1/stores/BV-BOK-02", json={"shopify_location_id": PUNE})
    assert r.status_code == 503, r.text  # a retry, not a correction he can make
    assert "last-sent record could not be reset" in r.json()["detail"]
    assert _saved(db, "BV-BOK-02")["shopify_location_id"] == BOKARO
    # the retraction itself DID go out -- that half is idempotent and safe
    assert len([x for x in calls if "inventorySetQuantities" in x["query"]]) == 1
