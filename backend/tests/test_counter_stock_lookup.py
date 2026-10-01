"""D7b (owner ruling 2026-09-29): counter staff get a READ-ONLY stock lookup.

SALES_STAFF, SALES_CASHIER, CASHIER and OPTOMETRIST search a product by model,
name, brand, SKU or barcode and see how many are available at THIS shop, at
every other shop, and in transit -- colour and size variants included -- and
never a cost price, landed cost, supplier, bill or any money figure other than
MRP / selling price. Audit row F46: "Sales staff have no stock screen at all."

Each test below was a strict xfail reproducing one finding (the FINDINGS list
is the state of origin/main 6068802); the fix in routers/inventory/lookup.py
and accountability.py turned them all green. The rest were guards and stay so.

THE CONTRACT these tests pin (the build follows it; extra keys are fine unless
they carry money):

    GET /api/v1/inventory/lookup?q=<text>          (inventory package router)
    200 -> {"store_id": <this shop = the caller's active store>,
            "items": [{"product_id", "sku", "name", "brand", "model",
                       "color", "size", "mrp", "offer_price",
                       "stores": [{"store_id", "store_name",
                                   "available", "in_transit"}, ...]}]}

    `stores` lists EVERY physical shop (stores_util.physical_stores), zeros
    included, never an ONLINE store. `available` is what the till may sell
    there: StockRepository.sellable_filter, the filter find_available and the
    sale guard count (AVAILABLE and in date, one unit per stock_units row), so
    this shop's figure is the till tile's (GET /inventory/sellable, #1173);
    `in_transit` counts TRANSFERRED units heading TO that shop
    (claim_for_transfer stamps transfer_to_store_id), one unit per row too.
    The search IS ProductRepository.search_products (active only, the same
    answer as GET /products?search=) plus an exact SKU, product barcode,
    manufacturer GTIN (attributes.gtin) or IMS unit barcode, active only and
    listed FIRST, never cut by the 200-row family cap; a hit brings
    its model family (find_similar_products' identity_key rule for colours,
    variant_of for sizes); an inactive product never comes back. No database
    -> 200 with no items (the real-app test runs on a dev box's
    MockDatabase), never a 500.

PANEL ROUND 2 (2026-10-01) moved `available` from item_events.on_hand_match
to the till's rule: a legacy lowercase / status-less or an expired unit is on
hand, but the till refuses to sell it, and the counter must not promise it.
It also dropped D7b-2's own name-word and attributes.gtin search: the lookup
now IS search_products, so it answers a text exactly as GET /products?search=
(the till's search) does. Widening that one search is a till change -- the
owner's call, not this screen's.

PANEL ROUND 3 (2026-10-01) put the manufacturer GTIN back as an EXACT match
(attributes.gtin, the public barcode; not a widening of search_products), and
made every exact match (SKU, product barcode, GTIN, unit label) come first and
escape the caps: a contact-lens model is one product per power, and a scan of
one power was cut from a 230-power family. An empty search answers nothing.

FINDINGS (traced in code on origin/main 6068802)

D7b-1  No stock read a counter role can reach. /inventory/cross-store-stock
       (routers/inventory/accountability.py:114-121) is gated
       require_roles(*_INVENTORY_ROLES), and _INVENTORY_ROLES
       (routers/inventory/_shared.py:84-90) leaves out every counter role;
       /endless-aisle/availability is manager-only behind a flag
       (routers/endless_aisle.py:134-144); /inventory-balancing is managers
       only (routers/inventory_balancing.py:31); GET /inventory/stock pins the
       caller to their own shop (routers/inventory/stock.py:74,
       validate_store_access). Frontend: INVENTORY_MODULE_ROLES
       (pages/inventory/inventoryRoles.ts:21-28) excludes them and navConfig.ts
       has no stock row for them (see the frontend half,
       pages/inventory/__tests__/stockLookupReachable.test.ts).
D7b-2  Search. The product search to reuse is BaseRepository.search over
       ProductRepository.SEARCH_FIELDS = brand, model, sku, variant, barcode
       (database/repositories/product_repository.py:47,
       base_repository.py:365-388: case-insensitive prefix per token, anchored
       at the START of each field). It does NOT look at the minted `name`
       (services/product_master.py:1584-1594, "{Brand} {Model} {Shape} ... -
       {Colour}", product_naming.py:385) -- and adding `name` to the fields is
       not enough, a shape or colour word is never at the start of it -- nor
       at a manufacturer GTIN kept in attributes.gtin (product_master.py:1327),
       or at the IMS UNIT barcode on the frame's label, which lives on
       stock_units.barcode and is resolved only by StockRepository.
       find_by_barcode (product_repository.py:296) behind the POS scan route
       (routers/inventory/stock_lookups.py:75).
D7b-3  Counts. One on-hand rule exists (services/item_events.py:214
       on_hand_match / :256 is_on_hand) and per-(product, shop) it is already
       answered by services/inventory_balancing.py:262
       _on_hand_by_product_store; the shop list is stores_util.physical_stores
       (services/stores_util.py:98). In transit = a unit TRANSFERRED with
       transfer_to_store_id (product_repository.py:799-821) -- nothing reads it
       per shop today.
D7b-4  Variants. Each colour is its own product (identity brand+model+colour,
       product_master.py:1547-1557) and each eye size is a `variant_of` child
       (product_master.py:2461-2516); a scan of one frame must bring its
       colours and sizes.
D7b-5  Money. The ledger row a lookup might reuse carries last_grn (a GRN
       number, stock.py:127-202 / :450) and the product doc carries cost_price,
       landed_cost, moving_avg_cost, purchase_price; cost_mask on main does not
       strip landed cost (services/cost_mask.py:25, #1161 widens it). The
       lookup must carry no cost, supplier or bill field at all.
D7b-6  Read-only: the lookup path answers GET only.
D7b-7  (frontend) a menu row, a route and an e2e layout-gate row for the
       counter roles -- pages/inventory/__tests__/stockLookupReachable.test.ts.
D7b-8  The existing cross-store read keeps a SECOND on-hand rule and a row that
       disagrees with its gate: accountability.py:131-137 matches the literal
       "status": "AVAILABLE" (a legacy lowercase / padded / IN_STOCK /
       status-less unit is invisible to it), and its rbac row
       (services/rbac_policy/rows_items_jarvis.py:249-261) admits SALES_STAFF /
       SALES_CASHIER, whom the route 403s, and refuses WORKSHOP_STAFF, whom the
       route admits. One rule, one implementation: it must read the same
       per-shop count as the lookup.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_counter_stock_lookup.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.dependencies as deps  # noqa: E402
from api.routers import inventory as inv_mod  # noqa: E402
from api.routers.auth import ALGORITHM, SECRET_KEY, get_current_user  # noqa: E402
from api.services.product_master import compute_identity_key  # noqa: E402
from api.services.rbac_policy import check_access  # noqa: E402
from api.services.rbac_policy._core import ALL_ROLES  # noqa: E402

# ONE corpus of every shape a stock_units row is stored in (the on-hand probe's).
# Here the answer is not the corpus's hand-written on-hand flag but what the
# till's own find_available sells of it -- the counter's number is the till's.
from test_on_hand_is_one_rule import _SHAPES, _DBProxy  # noqa: E402

LOOKUP_API = "/api/v1/inventory/lookup"
COUNTER_ROLES = ["SALES_STAFF", "SALES_CASHIER", "CASHIER", "OPTOMETRIST"]

S1, S2, S3 = "S1-DHANBAD", "S2-BOKARO", "S3-PUNE"
ONLINE, CLOSED = "BV-ONLINE-01", "S9-CLOSED"
PHYSICAL = {S1, S2, S3}

P54, P56, P003, RB = "P-807-54", "P-807-56", "P-003-54", "P-RB3025"
UNIT_BARCODE = "BV91FA3858A2"  # the IMS label on one Dhanbad frame of P54
GTIN_TOP = "8056597012345"  # P54's manufacturer barcode, top-level field
GTIN_ATTR = "8056597054321"  # P54's GTIN as the create door stores it (attributes.gtin)

# What must never reach a counter screen: every value seeded below as a cost,
# a supplier or a bill reference.
_SECRETS = [
    "1234.5", "1357.25", "135725", "1470.75", "1580.5", "1599",
    "Luxottica", "27AAACL1234F1Z5", "V-LUX", "GRN-2026-0042", "GRN-0042",
    "LUX/INV/7788",
]
_MONEY_KEY = re.compile(
    r"cost|landed|margin|vendor|supplier|grn|bill|invoice|purchase|paid|valu|"
    r"amount|price|gstin",
    re.I,
)
_ALLOWED_MONEY_KEYS = {"mrp", "offer_price", "offerPrice", "selling_price"}


# ============================================================================
# Engine: the real Mongo CI runs against; mongomock on a dev box
# ============================================================================


@pytest.fixture(scope="module")
def _engine():
    from pymongo import MongoClient

    uri = os.getenv("MONGODB_URL") or os.getenv("MONGODB_URI") or "mongodb://localhost:27017"
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except Exception:  # noqa: BLE001
        import mongomock

        client = mongomock.MongoClient()
    yield client
    client.close()


@pytest.fixture
def mongo_db(_engine):
    name = f"ims_test_lookup_{uuid.uuid4().hex[:12]}"
    yield _engine[name]
    try:
        _engine.drop_database(name)
    except Exception:  # noqa: BLE001
        pass


class _Conn:
    """What deps.get_db() / database.connection.get_db() hand back."""

    def __init__(self, proxy):
        self.db = proxy
        self.is_connected = True

    def get_collection(self, name):
        return self.db.get_collection(name)


def _user(role: str, store: str = S1) -> Dict[str, Any]:
    return {
        "user_id": f"u-{role.lower()}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": [store],
        "active_store_id": store,
    }


@pytest.fixture
def call(mongo_db, monkeypatch):
    """GET against the REAL inventory package router, bound to this engine.
    Only the DB handle and the repositories are redirected; store access, the
    route gates and every count run for real."""
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )
    import database.connection as db_conn

    proxy = _DBProxy(mongo_db)
    conn = _Conn(proxy)
    monkeypatch.setattr(inv_mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(
        inv_mod, "get_stock_repository", lambda: StockRepository(mongo_db["stock_units"])
    )
    monkeypatch.setattr(
        inv_mod, "get_product_repository", lambda: ProductRepository(mongo_db["products"])
    )
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(db_conn, "get_db", lambda: conn)

    def _get(user: Dict[str, Any], path: str = "/inventory/lookup", **params):
        app = FastAPI()
        app.include_router(inv_mod.router, prefix="/inventory")
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app).get(path, params=params)

    return _get


# ============================================================================
# The shop floor: three physical shops, one online, one closed; one Carrera
# model in two colours and two eye sizes; an unrelated Ray-Ban.
# ============================================================================

_MONEY_ON_PRODUCT = {
    "cost_price": 1234.5,
    "landed_cost": 1357.25,
    "landed_cost_paise": 135725,
    "moving_avg_cost": 1470.75,
    "purchase_price": 1580.5,
    "last_purchase_price": 1599.0,
    "vendor_id": "V-LUX",
    "vendor_name": "Luxottica Supplier Pvt",
    "supplier_gstin": "27AAACL1234F1Z5",
    "pricing": {"mrp": 12990.0, "cost_price": 1234.5},
}
_MONEY_ON_UNIT = {
    "cost_price": 1234.5,
    "unit_cost": 1234.5,
    "landed_cost": 1357.25,
    "grn_id": "GRN-0042",
    "grn_number": "GRN-2026-0042",
    "vendor_id": "V-LUX",
    "purchase_invoice_no": "LUX/INV/7788",
}


def _product(pid, sku, name, color, size, **extra):
    doc = {
        "_id": pid,
        "product_id": pid,
        "sku": sku,
        "name": name,
        "brand": "Carrera",
        "model": "CA8895",
        "category": "SUNGLASS",
        "color": color,
        "size": size,
        "mrp": 12990.0,
        "offer_price": 11990.0,
        "is_active": True,
        "attributes": {"brand_name": "Carrera", "model_no": "CA8895", "colour_code": color},
        **_MONEY_ON_PRODUCT,
    }
    doc.update(extra)
    # What the create door mints (product_master.normalise_payload).
    doc["identity_key"] = compute_identity_key(doc["brand"], doc["model"], doc["color"], doc["size"])
    return doc


def _unit(pid, store, status: Any = "AVAILABLE", **extra):
    doc = {
        "stock_id": f"STK-{uuid.uuid4().hex[:12]}",
        "product_id": pid,
        "store_id": store,
        "barcode": f"BV{uuid.uuid4().hex[-10:].upper()}",
        "quantity": 1,
        "location_code": "DEFAULT",
        **_MONEY_ON_UNIT,
    }
    if status is not None:
        doc["status"] = status
    doc.update(extra)
    return doc


def _seed(db) -> None:
    db["stores"].insert_many(
        [
            {"store_id": S1, "store_code": "BV-DHN-01", "store_name": "Dhanbad", "store_type": "RETAIL", "is_active": True},
            {"store_id": S2, "store_code": "BV-BOK-01", "store_name": "Bokaro", "store_type": "RETAIL", "is_active": True},
            # a legacy shop doc with no is_active flag is still a physical shop
            {"store_id": S3, "store_code": "WO-PUN-01", "store_name": "Pune", "store_type": "RETAIL"},
            {"store_id": ONLINE, "store_code": ONLINE, "store_name": "Online", "store_type": "ONLINE", "is_active": True},
            {"store_id": CLOSED, "store_code": "BV-OLD-01", "store_name": "Closed shop", "store_type": "RETAIL", "is_active": False},
        ]
    )
    db["products"].insert_many(
        [
            _product(P54, "SG-CARRERA-CA8895-807-54", "Carrera CA8895 Aviator Unisex Sunglasses - Gold", "807", "54", barcode=GTIN_TOP,
                     attributes={"brand_name": "Carrera", "model_no": "CA8895", "colour_code": "807", "gtin": GTIN_ATTR}),
            _product(P56, "SG-CARRERA-CA8895-807-56", "Carrera CA8895 Aviator Unisex Sunglasses - Gold", "807", "56", variant_of=P54),
            _product(P003, "SG-CARRERA-CA8895-003-54", "Carrera CA8895 Aviator Unisex Sunglasses - Black", "003", "54"),
            {
                "_id": RB, "product_id": RB, "sku": "SG-RAYBAN-RB3025-001-58",
                "name": "Ray-Ban Classic RB3025 001 58", "brand": "Ray-Ban", "model": "RB3025",
                "category": "SUNGLASS", "color": "001", "size": "58", "mrp": 9990.0,
                "offer_price": 9990.0, "is_active": True,
                "identity_key": compute_identity_key("Ray-Ban", "RB3025", "001", "58"),
            },
        ]
    )
    db["stock_units"].insert_many(
        [
            # P54 at Dhanbad: 1 the till sells, 1 legacy lowercase the till
            # refuses, 1 reserved, 1 sold, 1 quarantined
            _unit(P54, S1, "AVAILABLE", barcode=UNIT_BARCODE),
            _unit(P54, S1, "available"),
            _unit(P54, S1, "RESERVED"),
            _unit(P54, S1, "SOLD"),
            _unit(P54, S1, "QUARANTINED"),
            # P54 at Bokaro: 1 sellable, 1 shipped to Dhanbad (in transit)
            _unit(P54, S2, "AVAILABLE"),
            _unit(P54, S2, "TRANSFERRED", transfer_id="T-1", transfer_to_store_id=S1),
            # P56 (the 56 mm size) at Pune: 1
            _unit(P56, S3),
            # P003 (black) at Bokaro: 3
            _unit(P003, S2), _unit(P003, S2), _unit(P003, S2),
            _unit(RB, S1),
        ]
    )
    db["grns"].insert_one(
        {
            "grn_number": "GRN-2026-0042", "store_id": S1, "status": "ACCEPTED",
            "vendor_id": "V-LUX", "created_at": datetime.now() - timedelta(days=1),
            "accepted_at": datetime.now() - timedelta(days=1),
            "items": [{"product_id": P54, "accepted_qty": 2, "unit_price": 1580.5}],
        }
    )
    db["vendors"].insert_one(
        {"vendor_id": "V-LUX", "legal_name": "Luxottica Supplier Pvt", "gstin": "27AAACL1234F1Z5"}
    )


def _ok(resp) -> Dict[str, Any]:
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:300]}"
    return resp.json()


def _items(body) -> Dict[str, Dict[str, Any]]:
    return {str(i.get("product_id")): i for i in body.get("items") or []}


def _stores(item) -> Dict[str, Dict[str, Any]]:
    return {str(s.get("store_id")): s for s in item.get("stores") or []}


def _counts(item) -> Dict[str, tuple]:
    return {
        sid: (int(s.get("available", -1)), int(s.get("in_transit", -1)))
        for sid, s in _stores(item).items()
    }


# ============================================================================
# D7b-1  a counter role reaches the lookup (policy row + route gate + route)
# ============================================================================


@pytest.mark.parametrize("role", COUNTER_ROLES)
def test_d7b1_every_counter_role_reaches_the_lookup_in_the_real_app(client, role):
    assert check_access("GET", LOOKUP_API, [role]), f"no rbac_policy row admits {role}"
    token = jwt.encode(
        {
            "sub": f"u-{role.lower()}", "user_id": f"u-{role.lower()}",
            "username": role.lower(), "roles": [role], "store_ids": [S1],
            "active_store_id": S1, "exp": datetime.utcnow() + timedelta(hours=1),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    resp = client.get(LOOKUP_API, params={"q": "CA8895"},
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, f"{role}: {resp.status_code} {resp.text[:200]}"


@pytest.mark.parametrize("role", ALL_ROLES)
def test_d7b1_the_lookup_row_matches_its_gate_for_every_role(call, mongo_db, role):
    # The route reads its gate from the row (lookup.STOCK_LOOKUP_ROLES); a
    # literal tuple typed back into the route that drops or adds a role makes
    # the row and the route disagree for that role, and this fails.
    resp = call(_user(role), q="")
    gate_admits = resp.status_code != 403
    row_admits = check_access("GET", LOOKUP_API, [role])
    assert gate_admits == row_admits, (
        f"{role}: the policy row says {'yes' if row_admits else 'no'}, "
        f"the route answers {resp.status_code}"
    )


def test_d7b1_no_database_is_an_empty_answer_not_a_500(call, mongo_db, monkeypatch):
    _seed(mongo_db)  # the repositories still answer; the raw handle does not
    monkeypatch.setattr(inv_mod, "_get_db", lambda: None)
    assert _ok(call(_user("CASHIER"), q="CA8895")) == {"store_id": S1, "items": []}


# ============================================================================
# D7b-2  search by model, name, brand, SKU, product barcode or unit barcode
# ============================================================================

_SEARCHES = [
    ("model", "CA8895", P54),
    ("brand, any case", "carrera", P54),
    ("sku", "SG-CARRERA-CA8895-807-54", P54),
    ("manufacturer barcode (top-level)", GTIN_TOP, P54),
    ("manufacturer GTIN (attributes.gtin)", GTIN_ATTR, P54),
    ("brand plus model", "carrera ca8895", P54),
    ("the IMS unit barcode on the frame's label", UNIT_BARCODE, P54),
]


@pytest.mark.parametrize("label,query,pid", _SEARCHES, ids=[s[0] for s in _SEARCHES])
def test_d7b2_the_counter_finds_the_frame_by(call, mongo_db, label, query, pid):
    _seed(mongo_db)
    found = _items(_ok(call(_user("SALES_STAFF"), q=query)))
    assert pid in found, f"searching by {label} ({query!r}) did not find {pid}: {sorted(found)}"
    assert RB not in found, f"searching by {label} ({query!r}) dragged in the Ray-Ban"


def test_d7b2_the_search_is_the_product_search(call, mongo_db, monkeypatch):
    # GET /products?search= and the lookup answer a text alike because the
    # lookup calls the SAME ProductRepository.search_products. A lookup that
    # ran its own search (BaseRepository.search with its own field list)
    # would find P54 here behind the stub's back.
    from database.repositories.product_repository import ProductRepository

    _seed(mongo_db)
    asked = []
    monkeypatch.setattr(
        ProductRepository, "search_products", lambda self, q, *a, **k: asked.append(q) or []
    )
    assert _ok(call(_user("CASHIER"), q="CA8895"))["items"] == []
    assert asked == ["CA8895"]


_DEAD = "P-DEAD"


@pytest.mark.parametrize(
    "label,query",
    [("its model", "CA8895"), ("its sku", "SG-CARRERA-CA8895-999-54"),
     ("its unit barcode", "BVDEAD000001"), ("its gtin", "8056597099990"),
     ("a live colour's sku", "SG-CARRERA-CA8895-807-54")],
)
def test_d7b2_an_inactive_product_never_comes_back(call, mongo_db, label, query):
    # is_active=False is a soft-deleted product (product_master.soft_delete_
    # product) or a provisional PO-line one; the till's active-only search
    # cannot bill it, so the counter must not be told it is in stock.
    _seed(mongo_db)
    mongo_db["products"].insert_one(
        _product(_DEAD, "SG-CARRERA-CA8895-999-54", "Carrera CA8895 - Grey", "999", "54",
                 is_active=False, attributes={"gtin": "8056597099990"})
    )
    mongo_db["stock_units"].insert_one(_unit(_DEAD, S2, barcode="BVDEAD000001"))
    items = _items(_ok(call(_user("CASHIER"), q=query)))
    assert _DEAD not in items, f"searching by {label}: {sorted(items)}"


@pytest.mark.parametrize("q", ["", "   "], ids=["empty", "blank"])
def test_d7b2_an_empty_search_answers_nothing(call, mongo_db, q):
    # search_products("") matches EVERY active product (base_repository's
    # empty-query rule), so without the guard a stray Enter would list the
    # whole catalogue with every shop's count.
    _seed(mongo_db)
    assert _ok(call(_user("CASHIER"), q=q)) == {"store_id": S1, "items": []}


# A contact-lens model is one product per power (owner 2026-09-28), so one
# model can hold more rows than the lookup's caps (50 search hits, 200 family).
_CL_POWERS = 230
_CL_TARGET_SKU = "CLOASYS-1"  # a prefix of every other power's SKU below
_CL_TARGET_GTIN = "0733905577766"
_CL_TARGET_UNIT = "BVCL00000001"


def _seed_contact_lens_family(db) -> str:
    """230 active powers of one model; the one the counter scans is stored
    LAST, so storage order puts it past both caps. Returns its product_id."""
    rows = []
    for i in range(_CL_POWERS):
        last = i == _CL_POWERS - 1
        pid = "P-CL-TARGET" if last else f"P-CL-{i:03d}"
        power = f"-{(i + 1) * 0.25:.2f}"
        rows.append({
            "_id": pid, "product_id": pid,
            "sku": _CL_TARGET_SKU if last else f"{_CL_TARGET_SKU}{i:03d}",
            "name": f"Acuvue Oasys {power}", "brand": "Johnson & Johnson",
            "model": "Acuvue Oasys", "category": "CONTACT_LENS", "size": power,
            "mrp": 1800.0, "offer_price": 1700.0, "is_active": True,
            "attributes": {"power": power, **({"gtin": _CL_TARGET_GTIN} if last else {})},
            "identity_key": compute_identity_key("Johnson & Johnson", "Acuvue Oasys", None, power),
        })
    db["products"].insert_many(rows)
    db["stock_units"].insert_one(_unit("P-CL-TARGET", S2, barcode=_CL_TARGET_UNIT))
    return "P-CL-TARGET"


@pytest.mark.parametrize(
    "label,query",
    [("its sku", _CL_TARGET_SKU), ("its gtin", _CL_TARGET_GTIN), ("its unit barcode", _CL_TARGET_UNIT)],
)
def test_d7b2_a_scanned_power_is_never_cut_from_a_big_family(call, mongo_db, label, query):
    _seed(mongo_db)
    target = _seed_contact_lens_family(mongo_db)
    items = _ok(call(_user("CASHIER", S1), q=query))["items"]
    pids = [i["product_id"] for i in items]
    assert target in pids, f"scanning {label}: the power is missing from {len(pids)} rows"
    assert pids[0] == target, f"scanning {label}: the scanned power is row {pids.index(target)}"
    assert _counts(items[0])[S2] == (1, 0), "and Bokaro's box of it is counted"
    assert len(pids) > 1, "its other powers still come with it"


# ============================================================================
# D7b-3  this shop, every other shop, in transit -- the till's own count
# ============================================================================


def test_d7b3_counts_at_this_shop_every_other_shop_and_in_transit(call, mongo_db):
    _seed(mongo_db)
    body = _ok(call(_user("SALES_STAFF", S1), q="CA8895"))
    assert body.get("store_id") == S1, "this shop is the caller's own shop"
    items = _items(body)

    p54 = items.get(P54) or {}
    assert set(_stores(p54)) == PHYSICAL, (
        "every physical shop, zeros included; never the online or a closed shop: "
        f"{sorted(_stores(p54))}"
    )
    assert all(s.get("store_name") for s in p54.get("stores") or []), "shops by name"
    assert _counts(p54) == {
        S1: (1, 1),  # AVAILABLE (not the legacy 'available'); the Bokaro unit on its way
        S2: (1, 0),  # the shipped unit is not on Bokaro's shelf any more
        S3: (0, 0),
    }
    assert _counts(items.get(P56) or {}) == {S1: (0, 0), S2: (0, 0), S3: (1, 0)}
    assert _counts(items.get(P003) or {}) == {S1: (0, 0), S2: (3, 0), S3: (0, 0)}

    # Another counter, another shop: same numbers, its own "this shop".
    other = _ok(call(_user("OPTOMETRIST", S2), q="CA8895"))
    assert other.get("store_id") == S2
    assert _counts(_items(other).get(P54) or {}) == _counts(p54)


# Every stored shape, plus the two the till's rule adds: a dated unit either
# side of its expiry, and a row carrying quantity > 1 (the till counts rows).
_TILL_SHAPES = [(label, shape) for label, shape, _sell, _phys in _SHAPES] + [
    ("AVAILABLE but past its expiry date", {"status": "AVAILABLE", "expiry_date": "2020-01-01"}),
    ("AVAILABLE and in date", {"status": "AVAILABLE", "expiry_date": "2099-12-31"}),
    ("AVAILABLE with quantity 3 on the row", {"status": "AVAILABLE", "quantity": 3}),
]


def _one_unit(mongo_db, shape) -> tuple:
    """A product with ONE stock_units row at Dhanbad stored exactly as `shape`;
    returns (product_id, sku, what the till's find_available sells of it)."""
    from database.repositories.product_repository import StockRepository

    pid = f"PRD-{uuid.uuid4().hex[:12]}"
    sku = f"SKU-{pid[-8:]}"
    mongo_db["products"].insert_one(
        {"_id": pid, "product_id": pid, "sku": sku, "brand": "Vogue", "model": "VO5123",
         "category": "FRAME", "mrp": 3000.0, "offer_price": 3000.0, "is_active": True}
    )
    unit = {"stock_id": f"STK-{uuid.uuid4().hex[:8]}", "product_id": pid, "store_id": S1,
            "barcode": f"BC-{uuid.uuid4().hex[:12]}", "quantity": 1}
    unit.update(shape)
    mongo_db["stock_units"].insert_one(unit)
    return pid, sku, StockRepository(mongo_db["stock_units"]).find_available(pid, S1)


@pytest.mark.parametrize("label,shape", _TILL_SHAPES, ids=[t[0] for t in _TILL_SHAPES])
def test_d7b3_this_shops_count_is_the_tills_count(call, mongo_db, label, shape):
    # The till tile and the sale guard read find_available; the lookup must
    # say the same number for this shop, or the counter promises a frame the
    # till refuses (expired, legacy status) or counts one row twice.
    _seed(mongo_db)
    pid, sku, till = _one_unit(mongo_db, shape)
    here = _stores(_items(_ok(call(_user("CASHIER", S1), q=sku))).get(pid) or {}).get(S1) or {}
    assert int(here.get("available", -1)) == till, (
        f"a unit stored as {label}: the till sells {till}, the lookup says "
        f"available={here.get('available')}"
    )


@pytest.mark.parametrize(
    "label,unit,in_transit",
    [
        ("shipped to Dhanbad", {"store_id": S2, "status": "TRANSFERRED"}, 1),
        ("a shipped row with quantity 3", {"store_id": S2, "status": "TRANSFERRED", "quantity": 3}, 1),
        ("received at Dhanbad, stamp left on", {"store_id": S1, "status": "AVAILABLE"}, 0),
        ("sold at Dhanbad after receipt", {"store_id": S1, "status": "SOLD"}, 0),
    ],
)
def test_d7b3_in_transit_is_a_transferred_unit_on_its_way(call, mongo_db, label, unit, in_transit):
    # Only a TRANSFERRED unit is on its way: once received (or sold) the
    # transfer_to_store_id stamp may stay on the row, and counting it would
    # show the frame twice -- on the shelf AND on the way. One unit per row,
    # as `available` counts them.
    _seed(mongo_db)
    pid, sku, _till = _one_unit(mongo_db, {"status": "SOLD"})
    mongo_db["stock_units"].insert_one(
        {"stock_id": f"STK-{uuid.uuid4().hex[:8]}", "product_id": pid, "quantity": 1,
         "transfer_id": "T-9", "transfer_to_store_id": S1, **unit}
    )
    here = _stores(_items(_ok(call(_user("CASHIER", S1), q=sku))).get(pid) or {}).get(S1) or {}
    assert int(here.get("in_transit", -1)) == in_transit, f"{label}: {here}"


def test_d7b3_a_stores_doc_without_a_store_id_is_skipped_not_a_500(call, mongo_db):
    _seed(mongo_db)
    mongo_db["stores"].insert_one(
        {"store_code": "BV-XX-01", "store_name": "No id", "store_type": "RETAIL", "is_active": True}
    )
    p54 = _items(_ok(call(_user("CASHIER"), q="CA8895"))).get(P54) or {}
    assert set(_stores(p54)) == PHYSICAL


# ============================================================================
# D7b-4  colour and size variants
# ============================================================================


def test_d7b4_one_model_lists_every_colour_and_size(call, mongo_db):
    _seed(mongo_db)
    items = _items(_ok(call(_user("SALES_STAFF"), q="CA8895")))
    assert {P54, P56, P003} <= set(items), sorted(items)
    assert RB not in items
    got = {pid: (items[pid].get("color"), str(items[pid].get("size"))) for pid in (P54, P56, P003)}
    assert got == {P54: ("807", "54"), P56: ("807", "56"), P003: ("003", "54")}


@pytest.mark.parametrize("label,query", [("unit barcode", UNIT_BARCODE),
                                         ("sku", "SG-CARRERA-CA8895-807-54")])
def test_d7b4_a_scanned_frame_brings_its_colours_and_sizes(call, mongo_db, label, query):
    _seed(mongo_db)
    items = _items(_ok(call(_user("SALES_STAFF"), q=query)))
    assert {P54, P56, P003} <= set(items), f"scan by {label}: {sorted(items)}"
    assert RB not in items


def test_d7b4_a_size_variant_comes_with_its_parent_whatever_its_model_spelling(call, mongo_db):
    # The variant_of door checks the category, not brand+model
    # (product_master._resolve_variant_parent), so a size typed "CA-8895" is
    # still a CA8895: the link, not the spelling, makes it family.
    _seed(mongo_db)
    mongo_db["products"].insert_one(
        _product("P-807-58", "SG-CARRERA-CA-8895-807-58", "Carrera CA-8895 Aviator - Gold",
                 "807", "58", model="CA-8895", variant_of=P54)
    )
    assert "P-807-58" in _items(_ok(call(_user("SALES_STAFF"), q="CA8895")))
    items = _items(_ok(call(_user("SALES_STAFF"), q="SG-CARRERA-CA-8895-807-58")))
    assert {P54, P56} <= set(items), sorted(items)


_SPELLINGS = [
    # (label, the colour searched for, its sibling as stored, the query)
    ("model typed with a hyphen", ("Carrera", "CA8895"), ("Carrera", "CA-8895"), "CA8895"),
    ("model typed with a space", ("Ray-Ban", "RB4350"), ("Ray-Ban", "RB 4350"), "RB4350"),
    ("Luxottica 0 prefix", ("Ray-Ban", "RB4350"), ("Ray-Ban", "0RB4350"), "RB4350"),
    # scanned by its own SKU, so the capitals sibling is not a search hit too
    ("brand in capitals", ("Ray-Ban", "RB4350"), ("RAY-BAN", "RB4350"), "SG-RB4350-BLK"),
]


@pytest.mark.parametrize("label,hit,sibling,query", _SPELLINGS, ids=[t[0] for t in _SPELLINGS])
def test_d7b4_a_colour_spelled_differently_is_still_the_model(call, mongo_db, label, hit, sibling, query):
    # Each colour is its own product; the create door folds case, separators
    # and the 0 prefix into identity_key (normalise_identity_component), and
    # find_similar_products lists siblings by it. Raw brand+model equality
    # would tell the counter Bokaro has no blue when it has one.
    _seed(mongo_db)
    mongo_db["products"].insert_many(
        [
            _product("P-HIT", f"SG-{hit[1]}-BLK", f"{hit[0]} {hit[1]} - Black", "BLK", "54",
                     brand=hit[0], model=hit[1]),
            _product("P-BLUE", f"SG-X-{uuid.uuid4().hex[:6]}", f"{sibling[0]} {sibling[1]} - Blue",
                     "BLU", "54", brand=sibling[0], model=sibling[1]),
        ]
    )
    mongo_db["stock_units"].insert_one(_unit("P-BLUE", S2))
    items = _items(_ok(call(_user("SALES_STAFF"), q=query)))
    assert "P-BLUE" in items, f"{label}: {sorted(items)}"
    assert _counts(items["P-BLUE"])[S2] == (1, 0)


# ============================================================================
# D7b-5  no cost, supplier or bill -- MRP and selling price only
# ============================================================================


def _keys(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}.{k}", str(k)
            yield from _keys(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _keys(v, f"{path}[{i}]")


def test_d7b5_no_cost_supplier_or_bill_reaches_the_counter(call, mongo_db):
    _seed(mongo_db)
    body = _ok(call(_user("SALES_STAFF"), q="CA8895"))
    p54 = _items(body).get(P54) or {}
    assert float(p54.get("mrp", 0)) == 12990.0 and float(p54.get("offer_price", 0)) == 11990.0, (
        "the counter sees MRP and the selling price"
    )
    money_keys = [p for p, k in _keys(body) if _MONEY_KEY.search(k) and k not in _ALLOWED_MONEY_KEYS]
    assert not money_keys, f"money / supplier / bill keys reach the counter: {money_keys}"
    text = json.dumps(body)
    leaked = [s for s in _SECRETS if s in text]
    assert not leaked, f"cost / supplier / bill values reach the counter: {leaked}"


# ============================================================================
# D7b-6  read-only
# ============================================================================


def test_d7b6_the_lookup_is_read_only(app):
    methods = set()
    for route in app.routes:
        path = getattr(route, "path", "") or ""
        if path == LOOKUP_API or path.startswith(LOOKUP_API + "/"):
            methods |= set(getattr(route, "methods", set()) or set())
    assert "GET" in methods, "the lookup route is missing"
    assert methods <= {"GET", "HEAD"}, f"the lookup writes: {sorted(methods)}"


# ============================================================================
# D7b-8  the existing cross-store read: one on-hand rule, a row that matches
# ============================================================================


@pytest.mark.parametrize("label,shape", _TILL_SHAPES, ids=[t[0] for t in _TILL_SHAPES])
def test_d7b8_cross_store_stock_counts_what_the_till_sells(call, mongo_db, label, shape):
    # One rule, one implementation: the BOPIS read and the counter lookup both
    # call lookup.sellable_by_product_shop (find_available's filter).
    pid, _sku, till = _one_unit(mongo_db, shape)
    body = _ok(call(_user("ADMIN"), "/inventory/cross-store-stock", product_id=pid))
    qty = {s.get("store_id"): int(s.get("available_qty", 0)) for s in body.get("stores") or []}
    assert qty.get(S1, 0) == till, (
        f"a unit stored as {label}: the till sells {till}, cross-store-stock says {qty.get(S1, 0)}"
    )


_ROW_VS_GATE = [
    "SALES_STAFF", "SALES_CASHIER", "WORKSHOP_STAFF", "CASHIER", "OPTOMETRIST",
    "ACCOUNTANT", "STORE_MANAGER",
]


@pytest.mark.parametrize("role", _ROW_VS_GATE)
def test_d7b8_cross_store_stock_row_matches_its_gate(call, mongo_db, role):
    resp = call(_user(role), "/inventory/cross-store-stock", product_id="P-NONE")
    gate_admits = resp.status_code != 403
    row_admits = check_access("GET", "/api/v1/inventory/cross-store-stock", [role])
    assert gate_admits == row_admits, (
        f"{role}: the policy row says {'yes' if row_admits else 'no'}, "
        f"the route answers {resp.status_code}"
    )
