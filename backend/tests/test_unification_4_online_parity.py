"""
IMS 2.0 -- Unification step-4: online-customer PARITY + phantom-profile fix
===========================================================================
Step-5 already made online_order_mapper delegate the phone-keyed dedup+create to
the ONE canonical ``customer_service.ensure_customer`` (source=ONLINE). Step-4
finishes the ONLINE-customer story the owner asked about -- "where/how is online
customer data stored and how is it segregated?" -- and pins it with tests:

  1. PARITY (one collection, one record).
     - An online buyer whose phone matches an EXISTING in-store customer resolves
       to that SAME single record (mobile dedup), and is tagged online+in-store
       (origin online; in-store activity proven by an in-store order on the same id).
     - A freshly online-created customer carries the canonical skeleton (the same
       shape an in-store walk-in gets from ensure_customer): channel=ONLINE,
       source=ONLINE, both mobile+phone, the full store-key set, is_active, the
       loyalty/patients fields -- so an online record is NOT a second-class shell.

  2. SEGREGATION BY TAG (not by silo).
     - The /customers list filter cleanly separates online-origin from walk-in via
       the canonical channel/source/shopify_customer_id tag -- both kinds live in
       the ONE customers collection.

  3. PHANTOM-PROFILE FIX.
     - An online order with NEITHER phone NOR email mints NO customer (the customer
       base is not polluted) AND leaves NO dangling Shopify-id link on the order:
       the order is recorded as a clearly-marked guest (customer_id None,
       is_guest_order True) with the buyer snapshot still on the order.

CI-robust: an in-memory Mongo emulator + monkeypatched dependency accessors; no
network, no real Mongo, no whole-JSON substring assertions.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_unif4")
os.environ["GST_PRICING_MODE"] = "inclusive"
os.environ["ONLINE_STORE_ID"] = "BV-ONLINE-01"

from api.services import online_order_mapper  # noqa: E402
from api.routers import customers as customers_router  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo emulator (mirrors test_online_order_mapper's shape).
# ---------------------------------------------------------------------------


class _DuplicateKeyError(Exception):
    pass


def _match(doc, filter_) -> bool:
    if not filter_:
        return True
    for k, expected in filter_.items():
        if k == "$or":
            if not any(_match(doc, sub) for sub in expected):
                return False
            continue
        if k == "$nor":
            if any(_match(doc, sub) for sub in expected):
                return False
            continue
        if k == "$and":
            if not all(_match(doc, sub) for sub in expected):
                return False
            continue
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$exists":
                    present = k in doc
                    if present != bool(op_val):
                        return False
                elif op == "$ne":
                    if actual == op_val:
                        return False
                elif op == "$nin":
                    if actual in op_val:
                        return False
                elif op == "$type":
                    if actual is None:
                        return False
                else:
                    return False
        else:
            if actual != expected:
                return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=-1):
        self._docs.sort(key=lambda d: d.get(key) or "", reverse=(direction == -1))
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        if n:
            self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter([dict(d) for d in self._docs])


class FakeCollection:
    def __init__(self, name, database):
        self._name = name
        self.database = database
        self.docs: list = []
        self._unique_fields: set = set()

    def create_index(self, keys, **kwargs):
        if kwargs.get("unique") and isinstance(keys, str):
            self._unique_fields.add(keys)
        return None

    def _violates_unique(self, doc) -> bool:
        for f in self._unique_fields:
            val = doc.get(f)
            if val is None:
                continue
            for d in self.docs:
                if d.get(f) == val:
                    return True
        if "_id" in doc:
            for d in self.docs:
                if d.get("_id") == doc.get("_id"):
                    return True
        return False

    def insert_one(self, doc):
        if self._violates_unique(doc):
            raise _DuplicateKeyError("E11000 duplicate key error")
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if _match(d, filter_):
                return dict(d)
        return None

    def find(self, filter_=None, projection=None):
        return _Cursor([d for d in self.docs if _match(d, filter_)])

    def count_documents(self, filter_=None):
        return len([d for d in self.docs if _match(d, filter_)])

    def find_one_and_update(self, filter_, update, upsert=False, return_document=None):
        target = None
        for d in self.docs:
            if _match(d, filter_):
                target = d
                break
        if target is None and upsert:
            target = dict(filter_)
            self.docs.append(target)
        if target is None:
            return None
        for op, fields in (update or {}).items():
            if op == "$inc":
                for k, v in fields.items():
                    target[k] = (target.get(k) or 0) + v
            elif op == "$set":
                for k, v in fields.items():
                    target[k] = v
        return dict(target)

    def update_one(self, filter_, update, upsert=False):
        for d in self.docs:
            if _match(d, filter_):
                for k, v in (update.get("$set") or {}).items():
                    d[k] = v
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            doc = dict(filter_)
            for k, v in (update.get("$set") or {}).items():
                doc[k] = v
            self.docs.append(doc)
            return type("R", (), {"modified_count": 0, "matched_count": 0})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections: dict = {}

    def __getitem__(self, name):
        return self.get_collection(name)

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection(name, self)
        return self._collections[name]

    # The CustomerRepository built off a connected handle reads `db.customers`.
    @property
    def customers(self):
        return self.get_collection("customers")


@pytest.fixture
def wired(monkeypatch):
    db = FakeDB()

    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository

    order_repo = OrderRepository(db.get_collection("orders"))
    customer_repo = CustomerRepository(db.get_collection("customers"))

    class _StoreRepo:
        def find_by_id(self, _store_id):
            return {"gstin": "", "state_code": "20"}

        def find_active(self, filter=None):
            return [{"store_id": "BV-ONLINE-01", "state_code": "20"}]

    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(deps, "get_product_repository", lambda: None)
    monkeypatch.setattr(deps, "get_store_repository", lambda: _StoreRepo())
    monkeypatch.setattr(deps, "get_customer_repository", lambda: customer_repo)

    return {
        "db": db,
        "orders": db.get_collection("orders"),
        "customers": db.get_collection("customers"),
        "customer_repo": customer_repo,
    }


def _frame_order(order_id, *, phone="+91 98765 43210", email="buyer@example.com",
                 cust_id=555, price="999.00"):
    """A Shopify orders/create payload: one 5%-GST frame line."""
    customer = {"first_name": "Ravi", "last_name": "Kumar"}
    if cust_id is not None:
        customer["id"] = cust_id
    if phone:
        customer["phone"] = phone
    payload = {
        "id": order_id,
        "name": f"#{order_id}",
        "financial_status": "paid",
        "fulfillment_status": None,
        "customer": customer,
        "shipping_address": {"province": "20", "province_code": "20"},
        "line_items": [
            {
                "id": 9001,
                "product_id": 7001,
                "variant_id": 999001,
                "title": "Ray-Ban Frame RB1234",
                "product_type": "Frames",
                "sku": "RB-1234",
                "quantity": 1,
                "price": price,
                "total_discount": "0.00",
            }
        ],
    }
    if phone:
        payload["phone"] = phone
    if email:
        payload["email"] = email
        customer["email"] = email
    return payload


# ===========================================================================
# 1. PARITY -- one collection, one record, canonical skeleton.
# ===========================================================================


def test_online_buyer_dedups_to_existing_instore_customer_and_is_both(wired):
    """An online buyer whose phone matches an EXISTING in-store customer resolves
    to that ONE record (no duplicate), tagged online+in-store: origin in-store,
    later online activity proven by the online order on the same customer_id."""
    # Seed an in-store walk-in (created via the canonical service, source POS).
    from api.services.customer_service import ensure_customer

    cid, created = ensure_customer(
        wired["db"], mobile="9876543210", name="Ravi K",
        store_id="BV-PUN-01", source="POS",
    )
    assert created is True
    before = len(wired["customers"].docs)

    # The SAME human now buys online with the same number.
    res = online_order_mapper.map_shopify_order(
        _frame_order(40001), wired["db"], topic="orders/create"
    )

    assert res["customer_id"] == cid, "online buyer deduped to the in-store record"
    assert len(wired["customers"].docs) == before, "no duplicate customer minted"

    # ONE record, carrying its in-store origin, now linked to an online order.
    cust = wired["customers"].find_one({"customer_id": cid})
    assert cust["source"] == "POS"  # origin preserved on a dedup match
    order = wired["orders"].find_one({"shopify_order_id": "40001"})
    assert order["customer_id"] == cid
    assert order["channel"] == "ONLINE"  # the online side of the same person


def test_online_created_customer_carries_canonical_skeleton(wired):
    """A brand-new online buyer gets the SAME canonical skeleton an in-store
    walk-in gets -- NOT a stripped second-class shell."""
    res = online_order_mapper.map_shopify_order(
        _frame_order(40002, cust_id=777), wired["db"], topic="orders/create"
    )
    cust = wired["customers"].find_one({"customer_id": res["customer_id"]})
    assert cust is not None
    # Online-origin tags.
    assert cust["channel"] == "ONLINE"
    assert cust["source"] == "ONLINE"
    assert cust["shopify_customer_id"] == "777"
    # Canonical skeleton parity (the same fields ensure_customer stamps in-store).
    assert cust["mobile"] == "9876543210"  # normalized
    assert cust["phone"] == "9876543210"
    assert cust["is_active"] is True
    assert cust["customer_type"] == "B2C"
    assert "loyalty_points" in cust and cust["loyalty_points"] == 0
    assert "patients" in cust
    # Visible under EVERY store-key a customer list might filter on.
    assert cust["home_store_id"] == "BV-ONLINE-01"
    assert cust["preferred_store_id"] == "BV-ONLINE-01"
    assert cust["primary_store_id"] == "BV-ONLINE-01"
    assert cust["store_ids"] == ["BV-ONLINE-01"]


# ===========================================================================
# 2. SEGREGATION BY TAG -- online-origin vs walk-in on the /customers list.
# ===========================================================================


def _stub_user(roles=("SUPERADMIN",), store=None):
    return {"roles": list(roles), "active_store_id": store, "id": "u1"}


def test_list_customers_channel_filter_separates_online_from_walkin(wired, monkeypatch):
    """The /customers list filter cleanly separates online-origin from walk-in,
    both living in the ONE customers collection."""
    repo = wired["customer_repo"]
    # An in-store walk-in (no online markers).
    repo.create({"customer_id": "W1", "name": "Walk In", "mobile": "9000000001",
                 "channel": "STORE", "source": "POS"})
    # An online buyer (canonical online tags).
    repo.create({"customer_id": "O1", "name": "Online One", "mobile": "9000000002",
                 "channel": "ONLINE", "source": "ONLINE"})
    # A LEGACY online buyer: no channel/source tag, only the Shopify linkage id.
    repo.create({"customer_id": "O2", "name": "Legacy Online", "mobile": "9000000003",
                 "shopify_customer_id": "abc123"})

    monkeypatch.setattr(customers_router, "get_customer_repository", lambda: repo)

    def _ids(channel):
        result = asyncio.run(
            customers_router.list_customers(
                search=None, customer_type=None, channel=channel,
                store_id=None, skip=0, limit=50, current_user=_stub_user(),
            )
        )
        return {c["customer_id"] for c in result["customers"]}

    # ONLINE -> both the tagged AND the legacy-shopify-id online buyers, NOT walk-in.
    assert _ids("ONLINE") == {"O1", "O2"}
    # STORE -> only the walk-in.
    assert _ids("STORE") == {"W1"}
    # No filter -> everyone (segregation is opt-in).
    assert _ids(None) == {"W1", "O1", "O2"}
    # Unknown channel value is ignored (never silently empties the list).
    assert _ids("garbage") == {"W1", "O1", "O2"}


def test_channel_clause_builder_shapes(wired):
    """Unit-pin the clause builder so the Mongo shape can't silently drift."""
    online = customers_router._build_channel_clause("shopify")
    assert "$or" in online
    store = customers_router._build_channel_clause("WALKIN")
    assert "$nor" in store
    assert customers_router._build_channel_clause("") is None
    assert customers_router._build_channel_clause("nope") is None
    # Row-level mirror used by the search path.
    assert customers_router._row_is_online_origin({"channel": "ONLINE"}) is True
    assert customers_router._row_is_online_origin({"shopify_customer_id": "x"}) is True
    assert customers_router._row_is_online_origin({"source": "POS"}) is False


# ===========================================================================
# 3. PHANTOM-PROFILE FIX -- no junk customer, order recorded as guest.
# ===========================================================================


def test_guest_order_no_phone_no_email_mints_no_customer(wired):
    """A Shopify order with NEITHER phone NOR email mints NO customer and leaves NO
    dangling Shopify-id link on the order -- it is recorded as a clearly-marked
    guest with the buyer snapshot still attached."""
    before = len(wired["customers"].docs)

    payload = _frame_order(40003, phone="", email="", cust_id=99999)
    res = online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create"
    )

    # Order WAS created (the sale must be recorded), but NO customer minted.
    assert res["status"] == "created"
    assert res["customer_id"] is None
    assert len(wired["customers"].docs) == before, "no phantom customer created"

    order = wired["orders"].find_one({"shopify_order_id": "40003"})
    assert order is not None
    # The raw Shopify customer.id (99999) must NOT linger as a dangling link.
    assert order["customer_id"] is None
    assert order["is_guest_order"] is True
    # Buyer snapshot still on the order (name carried through from the payload).
    assert order["customer_name"]


def test_matched_order_is_not_flagged_guest(wired):
    """A normal (identified) online order is NOT flagged as a guest order."""
    res = online_order_mapper.map_shopify_order(
        _frame_order(40004), wired["db"], topic="orders/create"
    )
    assert res["customer_id"]
    order = wired["orders"].find_one({"shopify_order_id": "40004"})
    assert order["customer_id"] == res["customer_id"]
    assert order.get("is_guest_order") is False


def test_email_only_buyer_still_becomes_a_customer(wired):
    """Step-4 only kills the NO-phone-AND-NO-email phantom: an email-only buyer is
    still a usable, dedupable identity and DOES become a customer (not a guest)."""
    before = len(wired["customers"].docs)
    payload = _frame_order(40005, phone="", email="solo@example.com", cust_id=12321)
    res = online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create"
    )
    assert res["customer_id"], "email-only buyer becomes a customer"
    assert len(wired["customers"].docs) == before + 1
    order = wired["orders"].find_one({"shopify_order_id": "40005"})
    assert order["customer_id"] == res["customer_id"]
    assert order.get("is_guest_order") is False
